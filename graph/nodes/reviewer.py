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
    prompt = f"""审核以下回答是否**直接回答了用户的问题**。

    任务：{state['current_task']}
    回答：{state['tool_result']}

    审核标准：
    - 如果回答包含**具体数字或结论**，通过
    - 如果回答只是描述"应该怎么分析"、"统计维度包括"这类空话，不通过
    - 如果回答完全跑题或空白，不通过

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