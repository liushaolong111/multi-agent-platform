"""ReAct 决策节点（Thought + Action）：LLM 看着已有回答与历史观测，决定下一步调不调工具。

与旧一次性 Tool 节点的本质区别：
- 旧版：Skill 出答案 → Tool 只决策一次 → 结束（generate-then-reflect 单轮）
- 新版：决策 → 执行 → 观测回喂 → 再决策 的循环（真 ReAct），
  模型可以基于上一轮工具返回的真实数据决定"够不够、要不要再查"。

终止条件（两道闸门，缺一不可）：
1. 模型自己决定不调工具（tool_calls 为空）→ 收敛
2. tool_rounds 达到 MAX_TOOL_ROUNDS → 强制熔断，防无限循环烧钱
"""
import logging

from app.agent.llm import safe_ainvoke_with_tools
from app.agent.nodes.tool import _TOOLS, _draw_chart, needs_chart
from app.agent.state import AgentState
from app.conf import config
from app.service.permission import scoped_region

logger = logging.getLogger(__name__)


def route_after_decide(state: AgentState) -> str:
    """条件边：有待执行调用 →（审批 | 直接执行）；无调用 → 去审核。"""
    pending = state.get("pending_tool_calls") or []
    if not pending:
        return "finish"
    if config.APPROVAL_MODE:
        return "approval"
    return "execute"


async def tool_decide_node(state: AgentState) -> dict:
    """ReAct 的 Thought+Action：输出 pending_tool_calls 交给条件边路由。"""
    current_task = state.get("current_task", "")
    plan = state.get("plan") or []
    all_tasks = "；".join(plan) if plan else current_task
    existing_result = state.get("tool_result", "")
    observations = state.get("tool_observations", "")
    rounds = state.get("tool_rounds", 0)

    # 熔断：达到最大轮数不再决策，直接收尾（防御性，正常被条件边拦住）
    if rounds >= config.MAX_TOOL_ROUNDS:
        logger.info("[ToolDecide] 已达最大工具轮数 %d，停止决策", rounds)
        return {"pending_tool_calls": [], "status": "tool_done"}

    # 确定性前置：图表关键词首轮直接绘图，不走 LLM（保证必触发）
    if rounds == 0 and needs_chart(plan, current_task):
        from app.agent.metrics import metrics

        chart = _draw_chart(
            scoped_region(state.get("user_role", ""), state.get("user_region", ""))
        )
        metrics.record_tool("draw_chart")
        logger.info("[ToolDecide] 命中图表关键词，直接调用 draw_chart")
        return {
            "pending_tool_calls": [],
            "tool_result": f"{existing_result}\n\n【工具补充数据】\n{chart}",
            "status": "tool_done",
        }

    # 观测历史注入：让模型基于前几轮工具返回的真实数据做下一轮判断
    obs_section = (
        f"\n\n【已获取的工具数据】\n{observations[-1500:]}" if observations else ""
    )
    prompt = f"""根据以下信息，判断是否需要调用工具来补充数据。
已有回答完整、无需补充时可以不调用任何工具。
如果需要精确数字（成本、预算执行率、问题闭环率、排名、占比等），优先用 query_business_database 现场查库。

全部子任务：{all_tasks}
已有回答：{existing_result[:200]}{obs_section}"""

    response = await safe_ainvoke_with_tools(prompt, _TOOLS)

    if response is None:
        logger.warning("[ToolDecide] 工具决策失败，结束工具循环")
        return {"pending_tool_calls": [], "status": "tool_done"}

    pending = [
        {"name": call["name"], "args": call["args"]}
        for call in response.tool_calls
        if call["name"] in {t.name for t in _TOOLS}
    ]
    if len(pending) < len(response.tool_calls):
        logger.warning("[ToolDecide] LLM 请求了未注册的工具，已过滤")

    logger.info(
        "[ToolDecide] 第 %d 轮决策：%s",
        rounds + 1,
        [c["name"] for c in pending] or "（不调工具，收敛）",
    )
    return {"pending_tool_calls": pending, "status": "tool_decided"}
