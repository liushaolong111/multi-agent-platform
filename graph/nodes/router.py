"""Router 节点：任务分类。"""
import logging

from graph.llm import safe_invoke
from graph.state import AgentState

import config

logger = logging.getLogger(__name__)


def router_node(state: AgentState) -> dict:
    prompt = f"""你是一个任务分类器。将用户输入分类为以下之一：
- data_analysis：数据分析任务
- report_generation：报告生成任务
- knowledge_query：知识库问答任务

只返回分类结果（一个英文词），不要其他任何内容。

用户输入：{state['user_input']}"""

    raw = safe_invoke(prompt, fallback="knowledge_query")
    # 清洗并校验输出，防止 LLM 返回多余内容
    task_type = raw.strip().splitlines()[0].strip().lower()
    if task_type not in config.VALID_TASK_TYPES:
        logger.warning("[Router] 非法分类结果 '%s'，回退为 knowledge_query", raw)
        task_type = "knowledge_query"

    logger.info("[Router] 分类结果：%s", task_type)
    return {"task_type": task_type, "status": "routed"}
