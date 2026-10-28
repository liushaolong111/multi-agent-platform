"""统一配置管理：所有环境变量集中在此读取并校验，避免散落在各模块中。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env（放在最前面，确保后续 os.getenv 能读到）
load_dotenv()

# 项目根目录（app/conf/config.py 往上三级），不依赖 os.chdir
BASE_DIR = Path(__file__).resolve().parents[2]

# ---- LLM ----
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))

# ---- Redis（短期记忆）----
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
SESSION_TTL = int(os.getenv("SESSION_TTL", "3600"))

# ---- PostgreSQL（长期记忆）----
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ---- 业务数据库（工程项目 / 成本 / 安全质量数据，Text-to-SQL 查询目标）----
# 默认 SQLite（本地开发零依赖）；生产切 MySQL / PostgreSQL：
#   BUSINESS_DATABASE_URL=mysql+pymysql://user:pass@host:3306/business_db?charset=utf8mb4
BUSINESS_DATABASE_URL = os.getenv(
    "BUSINESS_DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'business.db'}"
)

# ---- RAG ----
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
FAISS_INDEX_DIR = str(BASE_DIR / "faiss_index")
DATA_DIR = str(BASE_DIR / "data")
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
# MMR 精排：RRF 融合后取 RERANK_CANDIDATES 条，经 MMR 多样性精排取 RAG_TOP_K 条
RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", "12"))
MMR_LAMBDA = float(os.getenv("MMR_LAMBDA", "0.7"))  # 1=纯相关性, 0=纯多样性

# 公司级敏感文件关键词（RBAC 单一出口判定）：文件名命中即标记为
# visibility=company，region 受限角色（manager）不可检索、不可读取。
COMPANY_SENSITIVE_KEYWORDS = ("sync", "经营", "financial")

# ---- 工作流 ----
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))
# ReAct 工具循环：决策→执行→再决策的最大轮数，防模型无限调工具
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "3"))
# 人工审批：true 时高风险工具（业务库查询）执行前需 POST /chat/approve 确认
APPROVAL_MODE = os.getenv("APPROVAL_MODE", "false").lower() == "true"

# ---- MCP 工具总线 ----
# 开启后 Agent 的工具列表中会追加 MCP 工具总线暴露的工具（mysql/vector/erp）
ENABLE_MCP_TOOLS = os.getenv("ENABLE_MCP_TOOLS", "false").lower() == "true"

# ---- 可观测性（日志）----
# json：每行一个 JSON 对象（生产/容器，便于 ELK/Loki 采集）；其他值：彩色控制台输出
LOG_FORMAT = os.getenv("LOG_FORMAT", "console")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ---- 可观测性（LangSmith 链路追踪）----
# langchain 会自动读取以下环境变量，这里仅做显式化与状态查询
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "").lower() == "true"
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY", "")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "multi-agent-platform")

# ---- API 安全 ----
# 设置后，/chat 与 /chat/stream 需要请求头 X-API-Key 匹配；留空则不鉴权（本地开发友好）
API_KEY = os.getenv("API_KEY", "")
# 限流：单 IP 每分钟最多请求次数
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

# ---- JWT 用户认证与 RBAC ----
# AUTH_ENABLED=false 时保持旧的 body.user_id 行为（本地开发/CI 友好）
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-key-32bytes-change-in-prod!")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "120"))

# ---- 向量存储 ----
# faiss（默认，本地嵌入式）| qdrant（服务化，原生 metadata 过滤；不可达自动降级 faiss）
VECTOR_STORE = os.getenv("VECTOR_STORE", "faiss")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "knowledge_base")

# ---- 合法的任务类型枚举（用于 router 输出校验）----
VALID_TASK_TYPES = {"data_analysis", "report_generation", "knowledge_query"}


def check_llm_config() -> str | None:
    """返回缺失的配置项名称，全部存在时返回 None。"""
    if not LLM_API_KEY:
        return "DEEPSEEK_API_KEY"
    return None
