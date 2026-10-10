"""Reviewer 节点：审核回答质量，决定通过或打回重规划。"""
import logging

from graph.llm import safe_invoke
from graph.state import AgentState
from memory.long_term import store_long_term

logger = logging.getLogger(__name__)


def reviewer_node(state: AgentState) -> dict:
    prompt = f"""审核以下回答是否**直接回答了用户的问题**。

任务：{state['current_task']}
回答：{state['tool_result']}

审核标准：
- 如果回答包含**具体数字或结论**，通过
- 如果回答只是描述"应该怎么分析"、"统计维度包括"这类空话，不通过
- 如果回答完全跑题或空白，不通过

结论只能是一个词：
APPROVED 或 REVISE"""

    raw = safe_invoke(prompt, fallback="REVISE")
    verdict = "APPROVED" if "APPROVED" in raw.upper() else "REVISE"
    logger.info("[Reviewer] 审核结论：%s", verdict)

    # 审核通过时写入长期记忆，供后续会话召回
    if verdict == "APPROVED":
        store_long_term(
            user_id=state.get("user_id", "default_user"),
            session_id=state.get("session_id", "default_session"),
            content={
                "user_input": state.get("user_input", ""),
                "answer": state.get("tool_result", "")[:1000],
            },
        )

    return {
        "review_verdict": verdict,
        "final_answer": state["tool_result"],
        "status": "reviewed",
    }
