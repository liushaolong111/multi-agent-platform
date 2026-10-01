import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from langchain_openai import ChatOpenAI
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from graph.state import AgentState
from memory.short_term import get_session, update_session

llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0
)

# 全局加载一次向量库
embeddings = HuggingFaceEmbeddings(model_name="BAAI/bge-small-zh-v1.5")
vectorstore = FAISS.load_local(
    "faiss_index",
    embeddings,
    allow_dangerous_deserialization=True
)


def skill_node(state: AgentState) -> dict:
    current = state['plan'][0] if state.get('plan') else state['user_input']

    # 1. 从短期记忆读取上下文
    session_id = state.get("session_id", "default_session")
    session_data = get_session(session_id)
    memory_context = session_data.get("last_interaction", "")

    # 2. 用 RAG 检索相关文档
    docs = vectorstore.similarity_search(current, k=3)
    rag_context = "\n\n".join([d.page_content for d in docs])

    # 3. 构造 Prompt（融合记忆 + RAG + 当前任务）
    prompt = f"""你是一个企业数据分析助手。请基于以下信息完成子任务。

【历史对话记忆】
{memory_context if memory_context else "（无历史记录）"}

【相关知识片段】
{rag_context}

【当前子任务】
{current}

【要求】
- 优先使用提供的知识片段中的具体信息
- 用简洁的中文回答，300字以内
- 如果知识片段中确实没有相关信息，明确说明"""

    result = llm.invoke(prompt)
    print(f"[Skill] 当前子任务：{current}")
    print(f"[Skill] 检索到 {len(docs)} 个片段")

    # 4. 更新短期记忆
    update_session(session_id, "last_interaction", result.content[:500])

    return {
        "current_task": current,
        "retrieved_docs": rag_context[:500],
        "tool_result": result.content,
        "memory_context": memory_context,
        "status": "skill_executed"
    }