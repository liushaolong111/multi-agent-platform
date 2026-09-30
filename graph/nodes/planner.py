import os
import json
from langchain_openai import ChatOpenAI
from graph.state import AgentState

llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0
)


def planner_node(state: AgentState) -> dict:
    prompt = f"""你是一个任务规划器。将以下任务拆解为3-5个子任务，
以JSON数组格式返回，每个子任务是一个字符串。

任务类型：{state['task_type']}
用户输入：{state['user_input']}

只返回JSON数组，不要其他内容，不要用代码块包裹。
示例格式：["子任务1", "子任务2", "子任务3"]"""

    result = llm.invoke(prompt)
    content = result.content.strip().replace("```json", "").replace("```", "").strip()
    try:
        plan = json.loads(content)
    except Exception:
        plan = [state['user_input']]
    print(f"[Planner] 规划结果：{plan}")
    return {"plan": plan,
            "status": "planned",
            "iteration": state.get("iteration", 0) + 1
    }