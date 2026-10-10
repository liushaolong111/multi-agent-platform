"""共享 LLM 客户端：所有节点复用同一个 ChatOpenAI 实例，并提供带重试的安全调用。"""
import logging
import time

from langchain_openai import ChatOpenAI

import config

logger = logging.getLogger(__name__)

# 单例 LLM 客户端，所有节点复用
llm = ChatOpenAI(
    model=config.LLM_MODEL,
    base_url=config.LLM_BASE_URL,
    api_key=config.LLM_API_KEY,
    temperature=config.LLM_TEMPERATURE,
    timeout=config.LLM_TIMEOUT,
)


def safe_invoke(prompt: str, fallback: str = "") -> str:
    """带重试的 LLM 调用，失败时返回 fallback 而非抛异常。

    Args:
        prompt: 传给 LLM 的提示词
        fallback: 所有重试都失败时返回的兜底文本

    Returns:
        LLM 返回的文本内容，或 fallback
    """
    last_error = None
    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        try:
            result = llm.invoke(prompt)
            return result.content
        except Exception as e:
            last_error = e
            logger.warning(
                "[LLM] 第 %d/%d 次调用失败：%s", attempt, config.LLM_MAX_RETRIES, e
            )
            if attempt < config.LLM_MAX_RETRIES:
                time.sleep(2 ** (attempt - 1))  # 指数退避：1s, 2s, 4s
    logger.error("[LLM] 全部重试失败，使用兜底响应。最后错误：%s", last_error)
    return fallback
