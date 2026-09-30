import os
from langchain_openai import ChatOpenAI
from graph.state import AgentState

llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0
)


def reviewer_node(state: AgentState) -> dict:
    prompt = f"""审核以下回答是否基本完成了任务。

任务：{state['current_task']}
回答：{state['tool_result']}

审核标准（从宽）：
- 只要回答内容与任务相关，且有一定信息量，就通过
- 只有当回答完全跑题、空白或明显错误时，才要求重做

结论只能是一个词：
APPROVED 或 REVISE"""

    result = llm.invoke(prompt)
    verdict = "APPROVED" if "APPROVED" in result.content.upper() else "REVISE"
    print(f"[Reviewer] 审核结论：{verdict}")

    return {
        "review_verdict": verdict,
        "final_answer": state['tool_result'],
        "status": "reviewed"
    }