"""人工审批节点（Human-in-the-loop）：高风险工具调用执行前暂停图，等待人工确认。

实现机制（LangGraph 原生能力）：
- `interrupt()` 挂起图执行，状态由 checkpointer 持久化
- API 层把"待审批调用"推给前端，用户确认后 POST /chat/approve
- 恢复时 `Command(resume={"approved": bool})` 从断点继续，无需重跑前面节点

默认关闭（APPROVAL_MODE=false 时条件边直接跳过本节点），
本地开发/CI 测试不受影响；生产对高风险操作（业务库查询）开启。
"""
import logging

from langgraph.types import interrupt

from app.agent.nodes.tool import pending_high_risk_calls
from app.agent.state import AgentState

logger = logging.getLogger(__name__)


def approval_node(state: AgentState) -> dict:
    """审批闸门：有待审批的高风险调用 → interrupt 等待；批准放行 / 拒绝拦截。"""
    pending = state.get("pending_tool_calls") or []
    risky = pending_high_risk_calls(pending)

    # 没有高风险调用：直接放行（不需要人介入）
    if not risky:
        return {"approval_status": "approved", "status": "approved"}

    # 挂起图，把待审批内容暴露给调用方；resume 值即 interrupt 的返回值
    decision = interrupt(
        {
            "type": "approval_required",
            "calls": risky,
            "message": f"以下 {len(risky)} 个高风险操作需要人工确认后才会执行",
        }
    )

    if decision.get("approved"):
        logger.info("[Approval] 人工批准，放行 %d 个调用", len(pending))
        return {"approval_status": "approved", "status": "approved"}

    # 拒绝：清空待执行调用并记录，tool_exec 将收到空列表、循环自然收尾
    logger.info("[Approval] 人工拒绝，拦截 %d 个调用", len(pending))
    observations = state.get("tool_observations", "")
    note = "【人工审批】高风险操作已被用户拒绝，未执行。"
    return {
        "pending_tool_calls": [],
        "approval_status": "rejected",
        "tool_observations": f"{observations}\n\n{note}" if observations else note,
        "status": "rejected",
    }
