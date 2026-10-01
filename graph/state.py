from typing import Annotated, TypedDict, List
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


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