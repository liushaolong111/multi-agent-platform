"""MCP 工具总线 Server。

将所有外部能力（MySQL 查库、向量检索、ERP 数据对接）以 MCP Server
形式标准化暴露。任意 MCP 客户端可通过统一 tool_call 接口调用，
实现解耦与可替换。

运行方式：
    python -m app.mcp.server          # stdio 模式（默认）
    python -m app.mcp.server --http   # HTTP 模式（需 mcp[cli]）

工具清单：
    mysql_query              只读 SQL 查询（带 RBAC 区域过滤）
    mysql_query_materials    材料库存查询
    vector_search            向量检索 + RAG 上下文召回
    erp_fetch_purchase_orders  ERP 采购订单
    erp_fetch_inventory        ERP 库存余额
    erp_fetch_schedule         ERP 到货计划
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta

from mcp.server.mcpserver import MCPServer
from sqlalchemy import text

from app.client.vectorstore import multi_route_search
from app.repository.business import get_business_session, get_schema_description

mcp = MCPServer("engineering-mcp-bus")

# ---------------------------------------------------------------------------
# MySQL 工具
# ---------------------------------------------------------------------------

_READ_ONLY_PREFIXES = ("select", "show", "describe", "explain", "with")


def _is_read_only(sql: str) -> bool:
    stripped = sql.strip().lower()
    return any(stripped.startswith(p) for p in _READ_ONLY_PREFIXES)


@mcp.tool()
def mysql_query(sql: str, region: str | None = None) -> str:
    """执行只读 SQL 查询业务数据库（projects/cost_records/safety_issues/material_records）。

    Args:
        sql: 只读 SQL（SELECT/SHOW/DESCRIBE/EXPLAIN/WITH）
        region: 可选，区域过滤（华东/华北/华南/西南），传入后自动追加 WHERE region=?
    """
    if not _is_read_only(sql):
        return json.dumps({"error": "仅允许只读 SQL（SELECT/SHOW/DESCRIBE/EXPLAIN/WITH）"}, ensure_ascii=False)

    session = get_business_session()
    if session is None:
        return json.dumps({"error": "业务库不可用"}, ensure_ascii=False)

    try:
        if region:
            # 用子查询包装，兼容带 GROUP BY / ORDER BY / LIMIT 的复杂 SQL
            sql = f"SELECT * FROM ({sql}) AS _region_scoped WHERE region = '{region}'"
        result = session.execute(text(sql))
        rows = result.fetchall()
        cols = list(result.keys()) if hasattr(result, "keys") else [str(i) for i in range(len(rows[0]))] if rows else []
        data = [dict(zip(cols, row)) for row in rows]
        return json.dumps({"rows": len(data), "columns": cols, "data": data[:50]}, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        session.close()


@mcp.tool()
def mysql_query_materials(region: str | None = None) -> str:
    """查询材料库存（含到货滞后信息）。

    Args:
        region: 可选，区域过滤（华东/华北/华南/西南）
    """
    session = get_business_session()
    if session is None:
        return json.dumps({"error": "业务库不可用"}, ensure_ascii=False)

    try:
        from sqlalchemy import select

        from app.model.business import MaterialRecord
        stmt = select(MaterialRecord)
        if region:
            stmt = stmt.where(MaterialRecord.region == region)
        rows = session.execute(stmt).scalars().all()
        data = []
        for r in rows:
            lag = None
            if r.eta_date and r.plan_use_date:
                lag = (r.eta_date - r.plan_use_date).days
            data.append({
                "project_code": r.project_code, "material_category": r.material_category,
                "material_name": r.material_name, "stock_qty": r.stock_qty,
                "eta_date": str(r.eta_date), "plan_use_date": str(r.plan_use_date),
                "lag_days": lag, "supplier": r.supplier,
            })
        return json.dumps({"rows": len(data), "data": data[:50]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 向量检索工具
# ---------------------------------------------------------------------------

@mcp.tool()
def vector_search(query: str, k: int = 3) -> str:
    """向量检索（多路召回：分桶路由 + dense/sparse 混合 + RRF + MMR）。

    Args:
        query: 检索文本
        k: 返回条数（默认 3）
    """
    try:
        docs = multi_route_search(query, k=k)
        data = [{"content": d.page_content[:300], "metadata": d.metadata} for d in docs]
        return json.dumps({"rows": len(data), "data": data}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# ERP 工具（Mock，模拟外部 ERP 系统）
# ---------------------------------------------------------------------------

_PROJECTS = [
    ("BY-HD-001", "华东", "滨江智慧大厦"),
    ("BY-HD-002", "华东", "苏州工业园厂房"),
    ("BY-HB-001", "华北", "天津滨海新区综合体"),
    ("BY-HN-001", "华南", "深圳前海写字楼"),
    ("BY-XN-001", "西南", "成都天府新区住宅"),
]
_MATERIALS = ["钢结构", "混凝土", "钢筋", "电缆", "幕墙", "防水材料"]
_SUPPLIERS = ["宝钢集团", "海螺水泥", "沙钢集团", "远东电缆", "远大幕墙", "东方雨虹"]


def _rand_material_rows(n: int, region: str | None) -> list[dict]:
    pool = _PROJECTS if not region else [p for p in _PROJECTS if p[1] == region]
    rows = []
    for _ in range(n):
        code, reg, name = random.choice(pool)
        mat = random.choice(_MATERIALS)
        sup = random.choice(_SUPPLIERS)
        plan = date.today() + timedelta(days=random.randint(-10, 30))
        eta = plan + timedelta(days=random.randint(-5, 15))
        rows.append({
            "project_code": code, "project_name": name, "region": reg,
            "material": mat, "supplier": sup,
            "plan_use_date": str(plan), "eta_date": str(eta),
        })
    return rows


@mcp.tool()
def erp_fetch_purchase_orders(region: str | None = None) -> str:
    """从 ERP 系统获取采购订单（项目/材料/供应商/计划到货日期）。"""
    return json.dumps({"rows": 12, "data": _rand_material_rows(12, region)}, ensure_ascii=False)


@mcp.tool()
def erp_fetch_inventory(region: str | None = None) -> str:
    """从 ERP 系统获取库存余额（项目/材料/库存数量）。"""
    rows = _rand_material_rows(10, region)
    for r in rows:
        r["stock_qty"] = random.randint(50, 2000)
    return json.dumps({"rows": 10, "data": rows}, ensure_ascii=False)


@mcp.tool()
def erp_fetch_schedule(region: str | None = None) -> str:
    """从 ERP 系统获取到货计划（项目/材料/预计到货日期）。"""
    rows = _rand_material_rows(10, region)
    return json.dumps({"rows": 10, "data": rows}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Schema 描述（辅助工具）
# ---------------------------------------------------------------------------

@mcp.tool()
def get_db_schema() -> str:
    """获取业务库 schema 描述（用于 Text-to-SQL 上下文）。"""
    return get_schema_description()


if __name__ == "__main__":
    mcp.run()
