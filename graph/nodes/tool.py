import os
from langchain_openai import ChatOpenAI
from graph.state import AgentState

llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0
)


def tool_node(state: AgentState) -> dict:
    """决定是否需要调用外部工具（占位实现）"""
    prompt = f"""根据以下信息，判断是否需要调用外部工具。
如果需要，返回工具名称和参数（JSON格式）。
如果不需要，返回"no_tool"。

当前任务：{state['current_task']}
已有结果：{state['tool_result'][:200]}

可用工具：query_database（查询数据库）, read_file（读取文件）"""

    result = llm.invoke(prompt)
    tool_decision = result.content.strip()
    print(f"[Tool] 工具决策：{tool_decision}")

    # 不污染 tool_result，只更新状态
    return {
        "status": "tool_processed"
    }