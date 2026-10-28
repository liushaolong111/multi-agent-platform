"""Text-to-SQL 工具：把自然语言问题转成 SQL 查询业务数据库，结果交给 LLM 组织答案。

这是 Phase 1「数据闭环」的核心：Agent 不再依赖写死的 pandas 预计算，
而是现场生成 SQL 查真实业务库——用户问什么，就查什么。

安全设计（面试要点）：
1. 只读防护：validate_readonly_sql 静态校验 + 引擎层 READ ONLY 事务
2. 防爆量：无 LIMIT 自动追加，结果截断 100 行
3. 自愈重试：SQL 执行报错时，把错误回喂 LLM 修正一次（self-correction）
4. 优雅降级：业务库不可达 / LLM 失败时返回提示文本，不阻塞主流程
"""
import logging
import re
from contextvars import ContextVar

from langchain_core.tools import tool

from app.agent.llm import safe_invoke
from app.agent.metrics import metrics
from app.repository.business import execute_readonly_query, get_schema_description
from app.service.permission import data_scope_prompt, sql_within_scope

logger = logging.getLogger(__name__)

# 当前请求的用户上下文（tool_node 执行前注入，供 RBAC 数据权限校验）
# 用 ContextVar 而不是全局变量：并发请求之间隔离，不会互相串身份
_current_user: ContextVar[dict] = ContextVar(
    "text2sql_current_user", default={"role": "admin", "region": ""}
)


def set_current_user(user: dict):
    """tool_node 在派发工具前调用，注入当前请求用户身份。"""
    _current_user.set(user or {"role": "admin", "region": ""})


def current_user() -> dict:
    """返回当前请求的用户上下文（供其他工具做数据出口权限判定）。"""
    return _current_user.get()

_SQL_PROMPT = """你是一个 SQL 专家。根据业务库 schema，把用户的自然语言问题转成**一条 SELECT 查询**。

{schema}

【要求】
- 只输出 SQL 本身，不要任何解释、不要用 markdown 代码块包裹
- 只允许 SELECT（可用 WITH 子查询），禁止 INSERT/UPDATE/DELETE/DROP
- 聚合查询记得 GROUP BY；金额保留 2 位小数用 ROUND(x, 2)
- 月份过滤用 cost_month，如 WHERE cost_month >= '2026-01' AND cost_month <= '2026-06'
- 日期过滤用 found_date / start_date，如 WHERE found_date >= '2026-01-01'
- 限制返回行数：普通查询加 LIMIT 20，聚合统计可加 LIMIT 100

【用户问题】
{question}"""

_FIX_PROMPT = """上次生成的 SQL 执行报错了，请修正后重新输出**一条 SELECT 查询**（只输出 SQL 本身）。

{schema}

【用户问题】
{question}

【上次 SQL】
{bad_sql}

【报错信息】
{error}"""


def _extract_sql(llm_output: str) -> str:
    """从 LLM 输出中提取 SQL：剥离 markdown 代码块与前后解释文字。"""
    text = llm_output.strip()
    # 去掉 ```sql ... ``` 包裹
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1).strip()
    # 若仍带前导解释，取第一个 SELECT/WITH 到末尾
    kw = re.search(r"\b(SELECT|WITH)\b", text, re.IGNORECASE)
    if kw:
        text = text[kw.start():]
    return text.strip().rstrip(";")


def generate_sql(question: str, user: dict | None = None) -> str:
    """LLM 生成 SQL；失败返回空串。manager 角色注入区域数据权限硬约束。"""
    scope = data_scope_prompt(user or _current_user.get())
    prompt = _SQL_PROMPT.format(schema=get_schema_description(), question=question)
    if scope:
        prompt = f"{scope}\n\n{prompt}"
    raw = safe_invoke(prompt, fallback="")
    return _extract_sql(raw) if raw else ""


@tool
def query_business_database(question: str) -> str:
    """用自然语言查询工程项目业务数据库（项目、成本、质量安全问题）。

    适用于需要精确数字、排名、占比、趋势、预算执行率等事实性数据的问题。
    内部会把问题转成 SQL 执行真实数据库查询，返回精确结果。

    Args:
        question: 自然语言问题，如「2026 年上半年各区域的项目成本排名」
    """
    metrics.record_tool("text2sql_generate")
    user = _current_user.get()
    sql = generate_sql(question, user)
    if not sql:
        return "（SQL 生成失败，业务数据查询不可用）"

    # RBAC 第二道防线：prompt 约束之外的 SQL 静态越权校验
    if not sql_within_scope(sql, user):
        logger.warning(
            "[Text2SQL] 越权拦截：用户 %s（%s/%s）的 SQL 未带区域过滤：%s",
            user.get("user_id"), user.get("role"), user.get("region"), sql,
        )
        metrics.record_tool("text2sql_scope_blocked")
        return (
            f"（权限不足：你只能查询 {user.get('region')} 区域的数据，"
            "本次查询因未限定区域已被安全层拦截）"
        )

    logger.info("[Text2SQL] 问题：%s → SQL：%s", question, sql)
    result = execute_readonly_query(sql)

    # 自愈重试：执行报错时把错误回喂 LLM 修正一次
    if result.startswith("查询执行失败") or result.startswith("SQL 安全校验未通过"):
        logger.info("[Text2SQL] 首次执行失败，触发自愈重试")
        fix_prompt = _FIX_PROMPT.format(
            schema=get_schema_description(),
            question=question,
            bad_sql=sql,
            error=result,
        )
        fixed_raw = safe_invoke(fix_prompt, fallback="")
        if fixed_raw:
            fixed_sql = _extract_sql(fixed_raw)
            logger.info("[Text2SQL] 修正后 SQL：%s", fixed_sql)
            retry_result = execute_readonly_query(fixed_sql)
            if not retry_result.startswith(("查询执行失败", "SQL 安全校验未通过")):
                return f"【业务数据库查询结果】\n问题：{question}\nSQL：{fixed_sql}\n\n{retry_result}"
        return result

    return f"【业务数据库查询结果】\n问题：{question}\nSQL：{sql}\n\n{result}"
