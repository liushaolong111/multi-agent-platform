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
    prompt = f"""你是一个任务规划器。将以下任务拆解为2-4个**具体可执行**的子任务，
    以JSON数组格式返回。

    任务类型：{state['task_type']}
    用户输入：{state['user_input']}

    要求：
    - 每个子任务必须是**直接可以执行的**，不要写"明确XX"这种抽象任务
    - 例如"搞个总的销售"应该拆成：["计算总销售额", "列出分产品销售额"]
    - 只返回JSON数组，不要其他内容

    示例格式：["子任务1", "子任务2"]"""

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