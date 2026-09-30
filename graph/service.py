from langgraph.graph import StateGraph, START, END
from graph.state import AgentState
from graph.nodes.router import router_node
from graph.nodes.planner import planner_node
from graph.nodes.skill import skill_node
from graph.nodes.tool import tool_node
from graph.nodes.reviewer import reviewer_node


def should_revise(state: AgentState) -> str:
    if state.get("iteration", 0) >= 3:
        print(f"[Service] 已达最大循环次数 {state.get('iteration')}，强制结束")
        return "finish"
    if state.get("review_verdict") == "REVISE":
        return "revise"
    return "finish"


workflow = StateGraph(AgentState)

workflow.add_node("router", router_node)
workflow.add_node("planner", planner_node)
workflow.add_node("skill", skill_node)
workflow.add_node("tool", tool_node)
workflow.add_node("reviewer", reviewer_node)

workflow.add_edge(START, "router")
workflow.add_edge("router", "planner")
workflow.add_edge("planner", "skill")
workflow.add_edge("skill", "tool")
workflow.add_edge("tool", "reviewer")

workflow.add_conditional_edges(
    "reviewer",
    should_revise,
    {"revise": "planner", "finish": END}
)

graph = workflow.compile()