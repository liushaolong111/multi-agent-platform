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
    """返回业务库 schema 的自然语言描述，注入 Text-to-SQL 的 prompt。"""
    return """【业务数据库 schema】（PostgreSQL/SQLite 兼容语法）

表 1：sales_records（销售流水，一行一笔销售）
  - sale_date  DATE     销售日期，如 2026-03-15
  - product    VARCHAR  产品名称（如：星辰CRM、星辰ERP、星辰BI）
  - region     VARCHAR  销售区域（如：华北、华东、华南、西南）
  - amount     FLOAT    销售额，单位：元
  - quantity   INTEGER  销量，单位：件

表 2：feedback_records（客户反馈，一行一条反馈）
  - customer    VARCHAR  客户名称
  - rating      INTEGER  评分，1-5 整数
  - issue_type  VARCHAR  问题类型（如：功能缺陷、响应速度、易用性）
  - description TEXT     问题描述
  - created_at  DATETIME 反馈时间"""


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
