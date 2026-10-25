"""ReAct 执行节点（Act + Observation）：执行 tool_decide 产出的调用，观测累积进 state。

执行结果不直接拼进最终答案，而是写入 tool_observations：
- 下一轮 tool_decide 能看到真实数据，决定"够不够、要不要再查"
- 循环结束后由本节点把全部观测合并进 tool_result 交给 Reviewer
"""
import logging

from app.agent.metrics import metrics
from app.agent.nodes.text2sql import set_current_user
from app.agent.nodes.tool import _TOOL_MAP
from app.agent.state import AgentState
from app.conf import config

logger = logging.getLogger(__name__)


def route_after_exec(state: AgentState) -> str:
    """条件边：上轮执行了工具且未超轮数 → 回 tool_decide 再决策；否则去审核。"""
    if state.get("last_tool_executed", 0) > 0 and state.get(
        "tool_rounds", 0
    ) < config.MAX_TOOL_ROUNDS:
        return "continue"
    return "finish"


def tool_exec_node(state: AgentState) -> dict:
    """执行 pending_tool_calls，累积观测；全部轮次结束后合并进 tool_result。"""
    pending = state.get("pending_tool_calls") or []
    observations = state.get("tool_observations", "")
    rounds = state.get("tool_rounds", 0)

    # 注入当前用户身份（RBAC 数据权限）：state 由 API 层从 JWT 解析后写入
    set_current_user(
        {
            "user_id": state.get("user_id", ""),
            "role": state.get("user_role", "admin"),
            "region": state.get("user_region", ""),
        }
    )

    outputs = []
    for call in pending:
        tool_fn = _TOOL_MAP.get(call["name"])
        if tool_fn is None:
            logger.warning("[ToolExec] 未注册的工具：%s", call["name"])
            continue
        result = tool_fn.invoke(call["args"])
        outputs.append(f"◆ {call['name']}({call['args']})：\n{result}")
        metrics.record_tool(call["name"])
        logger.info("[ToolExec] 调用 %s 完成", call["name"])

    rounds += 1
    if outputs:
        observations = (
            f"{observations}\n\n" if observations else ""
        ) + "\n\n".join(outputs)

    update = {
        "pending_tool_calls": [],
        "tool_observations": observations,
        "tool_rounds": rounds,
        "last_tool_executed": len(outputs),
        "status": "tool_executed",
    }

    # 循环收尾（下一轮将被路由到 reviewer）：把观测合并进最终答案材料
    will_continue = len(outputs) > 0 and rounds < config.MAX_TOOL_ROUNDS
    if not will_continue and observations:
        existing = state.get("tool_result", "")
        update["tool_result"] = (
            f"{existing}\n\n【工具补充数据】\n{observations}"
        )
    return update
