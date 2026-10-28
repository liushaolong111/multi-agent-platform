"""业务库仓储：引擎与会话管理、schema 描述、只读 SQL 执行。

设计要点：
- 独立于对话记忆库（app/repository/long_term.py），配置项为 BUSINESS_DATABASE_URL
- 数据库不可达时优雅降级：业务查询工具返回提示，不影响主流程
- execute_readonly_query 强制只读：拒绝一切非 SELECT 语句，防注入/防误写
"""
import logging
import re

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.conf import config
from app.model.business import Base

logger = logging.getLogger(__name__)

# SQL 安全：只允许单条 SELECT/WITH 语句，拦截写操作与多语句注入
_FORBIDDEN_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|"
    r"GRANT|REVOKE|ATTACH|DETACH|PRAGMA|VACUUM)\b",
    re.IGNORECASE,
)

# 单条查询最多返回行数，防止 LLM 生成无 LIMIT 的大查询拖垮服务
MAX_RESULT_ROWS = 100


def _create_engine():
    if not config.BUSINESS_DATABASE_URL:
        logger.warning(
            "[BusinessDB] BUSINESS_DATABASE_URL 未配置，业务查询功能不可用。"
        )
        return None
    try:
        engine = create_engine(config.BUSINESS_DATABASE_URL)
        Base.metadata.create_all(engine)
        return engine
    except Exception as e:
        logger.warning("[BusinessDB] 业务库连接失败（不可达？）：%s", e)
        return None


business_engine = _create_engine()
_BusinessSession = sessionmaker(bind=business_engine) if business_engine else None


def get_business_session():
    """获取业务库会话；不可用时返回 None（调用方需判空降级）。"""
    return _BusinessSession() if _BusinessSession else None


def get_schema_description() -> str:
    """返回业务库 schema 的自然语言描述，注入 Text-to-SQL 的 prompt。

    按当前连接方言附加日期函数提示，避免模型在 MySQL 上生成 strftime 之类的
    SQLite 专有函数。
    """
    dialect = business_engine.dialect.name if business_engine else "unknown"
    hint = {
        "mysql": "日期格式化用 DATE_FORMAT(found_date, '%Y-%m')",
        "postgresql": "日期格式化用 to_char(found_date, 'YYYY-MM')",
        "sqlite": "日期格式化用 strftime('%Y-%m', found_date)",
    }.get(dialect)

    body = """表 1：projects（工程项目，一行一个项目）
  - project_code       VARCHAR  项目编号，如 BY-HD-001
  - project_name       VARCHAR  项目名称
  - region             VARCHAR  区域公司：华东 / 华北 / 华南 / 西南
  - manager_name       VARCHAR  项目经理
  - status             VARCHAR  项目状态：在建 / 竣工 / 停工
  - contract_amount    FLOAT    合同金额，单位：元
  - start_date         DATE     开工日期，如 2025-03-01
  - planned_end_date   DATE     计划竣工日期

表 2：cost_records（成本流水，一行 = 项目 × 月份 × 成本类别）
  - project_code       VARCHAR  项目编号
  - project_name       VARCHAR  项目名称
  - region             VARCHAR  区域公司：华东 / 华北 / 华南 / 西南
  - cost_month         VARCHAR  成本月份，字符串 '2026-01' ~ '2026-06'，可直接比较大小
  - cost_category      VARCHAR  成本类别：材料 / 人工 / 机械 / 分包
  - amount             FLOAT    实际成本，单位：元
  - budget_amount      FLOAT    预算金额，单位：元

表 3：safety_issues（质量安全问题，一行一条隐患）
  - project_code       VARCHAR  项目编号
  - project_name       VARCHAR  项目名称
  - region             VARCHAR  区域公司：华东 / 华北 / 华南 / 西南
  - issue_type         VARCHAR  问题类型：安全 / 质量
  - severity           VARCHAR  严重程度：一般 / 较大 / 重大
  - status             VARCHAR  整改状态：待整改 / 整改中 / 已闭环
  - found_date         DATE     发现日期
  - closed_date        DATE     整改完成日期，未闭环时为 NULL
  - description        TEXT     问题描述

表 4：material_records（材料库存，一行 = 项目 × 材料）
  - project_code       VARCHAR  项目编号
  - project_name       VARCHAR  项目名称
  - region             VARCHAR  区域公司：华东 / 华北 / 华南 / 西南
  - material_category  VARCHAR  材料类别：钢结构 / 预制构件 / 水泥 / 钢筋 / 防水材料 / 幕墙 / 木材 / 砌块 / 机电设备 / 保温材料
  - material_name      VARCHAR  材料名称
  - specification      VARCHAR  规格型号
  - unit               VARCHAR  单位
  - stock_qty          INT      库存数量
  - eta_date           DATE     预计到货日期
  - plan_use_date      DATE     计划使用日期
  - supplier           VARCHAR  供应商

滞后判定：当 eta_date > plan_use_date 时，材料到货滞后，影响天数 = eta_date - plan_use_date"""

    header = f"【业务数据库 schema】（当前方言：{dialect}）\n"
    if hint:
        header += f"  语法提示：{hint}。\n"
    return header + body


def validate_readonly_sql(sql: str) -> str | None:
    """校验 SQL 是否安全的只读查询。

    Returns:
        None 表示合法；否则返回拒绝原因（人类可读）。
    """
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        return "SQL 为空"
    # 多语句拦截（允许语句内出现分号的情况极罕见，直接拒绝更安全）
    if ";" in cleaned:
        return "只允许单条 SQL 语句"
    first_word = cleaned.split(None, 1)[0].upper()
    if first_word not in ("SELECT", "WITH"):
        return f"只允许 SELECT 查询，收到：{first_word}"
    if _FORBIDDEN_PATTERN.search(cleaned):
        return "SQL 包含禁止的写操作关键字"
    return None


def execute_readonly_query(sql: str) -> str:
    """校验并执行只读 SQL，返回格式化结果文本。

    三层防护：
    1. validate_readonly_sql 静态校验（关键字 + 单语句）
    2. 强制追加 LIMIT（无 LIMIT 时），防爆量
    3. 结果行数截断 + 执行异常兜底
    """
    if not business_engine:
        return "（业务数据库不可用，请检查 BUSINESS_DATABASE_URL 配置）"

    cleaned = sql.strip().rstrip(";").strip()
    error = validate_readonly_sql(cleaned)
    if error:
        return f"SQL 安全校验未通过：{error}"

    # 无 LIMIT 时强制追加，防止全表扫描返回
    if not re.search(r"\bLIMIT\s+\d+", cleaned, re.IGNORECASE):
        cleaned = f"{cleaned} LIMIT {MAX_RESULT_ROWS}"

    try:
        with business_engine.connect() as conn:
            # 只读事务：PostgreSQL 上进一步从引擎层面禁写
            if business_engine.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))
            rows = conn.execute(text(cleaned)).fetchall()
            columns = list(rows[0]._fields) if rows else []
    except Exception as e:
        logger.warning("[BusinessDB] SQL 执行失败：%s | SQL: %s", e, cleaned)
        return f"查询执行失败：{e}"

    if not rows:
        return "查询成功，但结果为空（0 行）。"

    header = " | ".join(columns)
    lines = [f"查询结果（{len(rows)} 行）：", header, "-" * len(header)]
    for row in rows[:MAX_RESULT_ROWS]:
        lines.append(" | ".join(str(v) for v in row))
    if len(rows) > MAX_RESULT_ROWS:
        lines.append(f"（已截断，仅显示前 {MAX_RESULT_ROWS} 行）")
    return "\n".join(lines)
