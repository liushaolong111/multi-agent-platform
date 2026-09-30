import os
import csv
from langchain_openai import ChatOpenAI
from graph.state import AgentState

llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0
)


def load_data_context() -> str:
    """读取 data/ 目录下的所有数据文件，拼成上下文"""
    context_parts = []
    data_dir = "data"

    if not os.path.exists(data_dir):
        return "（未找到 data 目录）"

    for filename in os.listdir(data_dir):
        filepath = os.path.join(data_dir, filename)
        if not os.path.isfile(filepath):
            continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            # 每个文件截取前 2000 字符，避免上下文过长
            context_parts.append(f"### 文件：{filename}\n{content[:2000]}")
        except Exception as e:
            print(f"[Skill] 读取 {filename} 失败：{e}")

    return "\n\n".join(context_parts)


def skill_node(state: AgentState) -> dict:
    current = state['plan'][0] if state.get('plan') else state['user_input']

    # 加载真实数据
    data_context = load_data_context()

    prompt = f"""你是一个企业数据分析助手。请基于以下真实数据完成子任务。

【可用数据】
{data_context}

【当前子任务】
{current}

【要求】
- 优先使用数据中的具体数字和事实
- 用简洁的中文回答，300字以内
- 如果数据中确实没有相关信息，明确说明"""

    result = llm.invoke(prompt)
    print(f"[Skill] 当前子任务：{current}")
    return {
        "current_task": current,
        "retrieved_docs": data_context[:500],  # 记录用了哪些数据
        "tool_result": result.content,
        "status": "skill_executed"
    }