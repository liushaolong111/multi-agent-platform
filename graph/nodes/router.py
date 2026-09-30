import os
from langchain_openai import ChatOpenAI
from graph.state import AgentState

llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0
)


def router_node(state: AgentState) -> dict:
    prompt = f"""你是一个任务分类器。将用户输入分类为以下之一：
- data_analysis：数据分析任务
- report_generation：报告生成任务
- knowledge_query：知识库问答任务

只返回分类结果，不要其他任何内容。

用户输入：{state['user_input']}"""

    result = llm.invoke(prompt)
    task_type = result.content.strip()
    print(f"[Router] 分类结果：{task_type}")
    return {"task_type": task_type, "status": "routed"}