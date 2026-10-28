"""RBAC 数据权限：角色 → 数据作用域判定。

所有数据出口（Text-to-SQL、预计算统计、知识检索、图表生成）共用这一套
scoped_region 原语，避免各出口各写一套权限逻辑而漏掉某条旁路。
"""
import re

from app.model.user import ROLE_MANAGER

# 带 region 字段的业务表：区域受限角色查询这些表时必须显式过滤区域
SCOPED_TABLES = ("projects", "cost_records", "safety_issues")


def data_scope_prompt(user: dict) -> str:
    """生成注入 Text-to-SQL prompt 的数据权限硬约束文本。"""
    if user.get("role") == ROLE_MANAGER and user.get("region"):
        return (
            f"【数据权限约束（最高优先级，不可违反）】\n"
            f"你是区域公司工程管理员，只能查询 region = '{user['region']}' 的数据。"
            f"凡涉及 projects、cost_records、safety_issues 三张表的 SQL 必须带 "
            f"WHERE region = '{user['region']}'（或包含该条件的 AND 子句），"
            f"否则将被安全层拒绝执行。"
        )
    return ""


def sql_within_scope(sql: str, user: dict) -> bool:
    """校验生成的 SQL 是否在用户数据权限内（prompt 约束之外的第二道防线）。"""
    if user.get("role") != ROLE_MANAGER or not user.get("region"):
        return True

    # manager 角色：凡碰带区域字段的业务表，必须带 region = '<本区域>' 过滤
    if not any(
        re.search(rf"\b{table}\b", sql, re.IGNORECASE) for table in SCOPED_TABLES
    ):
        return True
    pattern = rf"region\s*=\s*'{re.escape(user['region'])}'"
    return bool(re.search(pattern, sql, re.IGNORECASE))


def scoped_region(role: str, region: str) -> str | None:
    """数据作用域原语：region 受限角色返回本区域，其他角色返回 None。

    None 表示"无区域限制"（可见全量）。SQL 层、预计算统计层、知识检索层、
    图表生成层共用这一个判定，避免各数据出口各写一套权限逻辑而漏掉某条。
    """
    if role == ROLE_MANAGER and region:
        return region
    return None


def rag_scope_filter(role: str, region: str) -> dict | None:
    """RAG 检索层的权限过滤条件（与 SQL 层并列的数据出口防线）。

    region 受限角色只能命中 visibility=public 的文档，
    避免通过知识检索旁路拿到全公司/跨区域的聚合数据。
    """
    if scoped_region(role, region):
        return {"visibility": "public"}
    return None
