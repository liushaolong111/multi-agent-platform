"""统一配置管理：所有环境变量集中在此读取并校验，避免散落在各模块中。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env（放在最前面，确保后续 os.getenv 能读到）
load_dotenv()

# 项目根目录（api/main.py 的上一级），不依赖 os.chdir
BASE_DIR = Path(__file__).resolve().parent

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

# ---- RAG ----
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
FAISS_INDEX_DIR = str(BASE_DIR / "faiss_index")
DATA_DIR = str(BASE_DIR / "data")
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))

# ---- 工作流 ----
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))

# ---- 合法的任务类型枚举（用于 router 输出校验）----
VALID_TASK_TYPES = {"data_analysis", "report_generation", "knowledge_query"}


def check_llm_config() -> str | None:
    """返回缺失的配置项名称，全部存在时返回 None。"""
    if not LLM_API_KEY:
        return "DEEPSEEK_API_KEY"
    return None
