"""共享 LLM 客户端：所有节点复用同一个 ChatOpenAI 实例，并提供带重试的安全调用。"""
import asyncio
import logging
import time

from langchain_openai import ChatOpenAI

from app.agent.metrics import metrics
from app.conf import config

logger = logging.getLogger(__name__)

# 单例 LLM 客户端，所有节点复用
llm = ChatOpenAI(
    model=config.LLM_MODEL,
    base_url=config.LLM_BASE_URL,
    api_key=config.LLM_API_KEY,
    temperature=config.LLM_TEMPERATURE,
    timeout=config.LLM_TIMEOUT,
)


def _record_usage(result, latency_ms: float, error: bool = False):
    """从 LangChain 响应中提取 token 用量并记录到指标模块。"""
    usage = getattr(result, "usage_metadata", None) or {}
    metrics.record_llm_call(
        latency_ms=latency_ms,
        prompt_tokens=usage.get("input_tokens", 0),
        completion_tokens=usage.get("output_tokens", 0),
        error=error,
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
        start = time.perf_counter()
        try:
            result = llm.invoke(prompt)
            _record_usage(result, (time.perf_counter() - start) * 1000)
            return result.content
        except Exception as e:
            last_error = e
            metrics.record_llm_call((time.perf_counter() - start) * 1000, error=True)
            logger.warning(
                "[LLM] 第 %d/%d 次调用失败：%s", attempt, config.LLM_MAX_RETRIES, e
            )
            if attempt < config.LLM_MAX_RETRIES:
                time.sleep(2 ** (attempt - 1))  # 指数退避：1s, 2s, 4s
    logger.error("[LLM] 全部重试失败，使用兜底响应。最后错误：%s", last_error)
    return fallback


def safe_invoke_with_tools(prompt: str, tools: list):
    """带重试的原生 Function Calling 调用。

    Args:
        prompt: 决策提示词
        tools: langchain @tool 装饰的工具列表

    Returns:
        AIMessage（通过 .tool_calls 读取调用决策）；全部重试失败时返回 None
    """
    last_error = None
    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        start = time.perf_counter()
        try:
            result = llm.bind_tools(tools).invoke(prompt)
            _record_usage(result, (time.perf_counter() - start) * 1000)
            return result
        except Exception as e:
            last_error = e
            metrics.record_llm_call((time.perf_counter() - start) * 1000, error=True)
            logger.warning(
                "[LLM] 第 %d/%d 次工具调用失败：%s", attempt, config.LLM_MAX_RETRIES, e
            )
            if attempt < config.LLM_MAX_RETRIES:
                time.sleep(2 ** (attempt - 1))
    logger.error("[LLM] 工具调用全部重试失败。最后错误：%s", last_error)
    return None


async def safe_ainvoke(prompt: str, fallback: str = "") -> str:
    """safe_invoke 的异步版本：非阻塞事件循环，供异步节点与并发子任务使用。"""
    last_error = None
    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        start = time.perf_counter()
        try:
            result = await llm.ainvoke(prompt)
            _record_usage(result, (time.perf_counter() - start) * 1000)
            return result.content
        except Exception as e:
            last_error = e
            metrics.record_llm_call((time.perf_counter() - start) * 1000, error=True)
            logger.warning(
                "[LLM] 第 %d/%d 次异步调用失败：%s", attempt, config.LLM_MAX_RETRIES, e
            )
            if attempt < config.LLM_MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
    logger.error("[LLM] 异步调用全部重试失败，使用兜底响应。最后错误：%s", last_error)
    return fallback


async def safe_ainvoke_with_tools(prompt: str, tools: list):
    """safe_invoke_with_tools 的异步版本：ReAct 决策节点专用。"""
    last_error = None
    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        start = time.perf_counter()
        try:
            result = await llm.bind_tools(tools).ainvoke(prompt)
            _record_usage(result, (time.perf_counter() - start) * 1000)
            return result
        except Exception as e:
            last_error = e
            metrics.record_llm_call((time.perf_counter() - start) * 1000, error=True)
            logger.warning(
                "[LLM] 第 %d/%d 次异步工具调用失败：%s",
                attempt,
                config.LLM_MAX_RETRIES,
                e,
            )
            if attempt < config.LLM_MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
    logger.error("[LLM] 异步工具调用全部重试失败。最后错误：%s", last_error)
    return None
