from typing import Annotated, List, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """五阶段工作流的共享状态"""
    messages: Annotated[List[BaseMessage], add_messages]
    user_input: str
    task_type: str
    plan: List[str]
    current_task: str
    retrieved_docs: str
    tool_result: str
    review_verdict: str
    final_answer: str
    status: str
    iteration: int
    session_id: str  # ← 新增
    user_id: str  # ← 新增
    memory_context: str  # ← 新增
    user_role: str  # RBAC 角色：admin / manager / viewer（API 层从 JWT 解析写入）
    user_region: str  # manager 角色负责的区域，行级数据隔离依据
    # ---- ReAct 工具循环 ----
    pending_tool_calls: list  # tool_decide 产出、待 tool_exec 执行的调用 [{name, args}]
    tool_observations: str  # 累积的工具执行观测结果（多轮 ReAct 共享）
    tool_rounds: int  # 已执行的工具轮数（配合 MAX_TOOL_ROUNDS 终止循环）
    last_tool_executed: int  # 上一轮实际执行了几个工具（0 = 该停了）
    approval_status: str  # 人工审批状态：pending / approved / rejected / ""
    # ---- 多智能体专家协作 ----
    expert_analyses: dict  # 各专家分析结果 {"cost": "...", "safety": "...", "material": "..."}
    coordinated_answer: str  # 协调者聚合各专家后的综合回答
