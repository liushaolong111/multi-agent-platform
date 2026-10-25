"""LangGraph 工作流编排：Router → Planner → Skill → ReAct(ToolDecide ⇄ ToolExec) → Reviewer。

Phase 3 结构升级：
- 旧固定流水线 Skill → Tool(一次性) → Reviewer
- 新 ReAct 循环：tool_decide →(条件边)→ approval?→ tool_exec →(条件边回环)→ tool_decide
- 审批节点走 LangGraph interrupt，需 checkpointer 持久化断点状态（MemorySaver）
"""
import functools
import logging
import time

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.metrics import metrics
from app.agent.nodes.approval import approval_node
from app.agent.nodes.planner import planner_node
from app.agent.nodes.reviewer import reviewer_node
from app.agent.nodes.router import router_node
from app.agent.nodes.skill import skill_node
from app.agent.nodes.tool_decide import route_after_decide, tool_decide_node
from app.agent.nodes.tool_exec import route_after_exec, tool_exec_node
from app.agent.state import AgentState
from app.conf import config

logger = logging.getLogger(__name__)


def _timed(name: str, fn):
    """包装节点函数，记录每次执行耗时到指标模块（兼容同步与异步节点）。"""

    if hasattr(fn, "__code__") and fn.__code__.co_flags & 0x80:  # 协程函数

        @functools.wraps(fn)
        async def async_wrapper(state):
            start = time.perf_counter()
            try:
                return await fn(state)
            finally:
                metrics.record_node(name, (time.perf_counter() - start) * 1000)

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(state):
        start = time.perf_counter()
        try:
            return fn(state)
        finally:
            metrics.record_node(name, (time.perf_counter() - start) * 1000)

    return wrapper


def should_revise(state: AgentState) -> str:
    """条件边：达到最大迭代次数或审核通过则结束，否则打回 Planner。"""
    if state.get("iteration", 0) >= config.MAX_ITERATIONS:
        logger.info(
            "[Service] 已达最大循环次数 %d，强制结束", state.get("iteration")
        )
        return "finish"
    if state.get("review_verdict") == "REVISE":
        return "revise"
    return "finish"


workflow = StateGraph(AgentState)

workflow.add_node("router", _timed("router", router_node))
workflow.add_node("planner", _timed("planner", planner_node))
workflow.add_node("skill", _timed("skill", skill_node))
workflow.add_node("tool_decide", _timed("tool_decide", tool_decide_node))
workflow.add_node("approval", _timed("approval", approval_node))
workflow.add_node("tool_exec", _timed("tool_exec", tool_exec_node))
workflow.add_node("reviewer", _timed("reviewer", reviewer_node))

workflow.add_edge(START, "router")
workflow.add_edge("router", "planner")
workflow.add_edge("planner", "skill")
workflow.add_edge("skill", "tool_decide")

# ReAct 决策后：有调用 →（审批 | 执行）；无调用 → 审核
workflow.add_conditional_edges(
    "tool_decide",
    route_after_decide,
    {"approval": "approval", "execute": "tool_exec", "finish": "reviewer"},
)
workflow.add_edge("approval", "tool_exec")

# 执行后：还有信息缺口且未超轮数 → 回决策（Thought 循环）；否则 → 审核
workflow.add_conditional_edges(
    "tool_exec",
    route_after_exec,
    {"continue": "tool_decide", "finish": "reviewer"},
)

workflow.add_conditional_edges(
    "reviewer",
    should_revise,
    {"revise": "planner", "finish": END},
)

# checkpointer：interrupt 断点持久化（MemorySaver 进程内；生产可换 PostgresSaver）
graph = workflow.compile(checkpointer=MemorySaver())
