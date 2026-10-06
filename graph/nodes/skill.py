import os
import pandas as pd

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


def compute_stats() -> str:
    """用 pandas 精确计算销售数据的统计信息"""
    try:
        df = pd.read_csv("data/sales_q1.csv")

        # 基础统计
        total_sales = df["销售额"].sum()
        total_qty = df["数量"].sum()

        # 按产品统计
        by_product = df.groupby("产品")["销售额"].sum().to_dict()
        by_region = df.groupby("区域")["销售额"].sum().to_dict()

        stats = f"""【精确统计数据（由程序计算，可信）】
总销售额：{total_sales:,} 元
总销量：{total_qty} 件
记录数：{len(df)} 条

分产品销售额：
"""
        for product, amount in by_product.items():
            stats += f"  - {product}：{amount:,} 元\n"

        stats += "\n分区域销售额：\n"
        for region, amount in by_region.items():
            stats += f"  - {region}：{amount:,} 元\n"

        return stats
    except Exception as e:
        print(f"[Skill] 统计计算失败：{e}")
        return ""


def skill_node(state: AgentState) -> dict:
    current = state['plan'][0] if state.get('plan') else state['user_input']

    # 1. 从短期记忆读取上下文
    session_id = state.get("session_id", "default_session")
    session_data = get_session(session_id)
    memory_context = session_data.get("last_interaction", "")

    # 2. 用 RAG 检索相关文档
    docs = vectorstore.similarity_search(current, k=3)
    rag_context = "\n\n".join([d.page_content for d in docs])

    # 3. 预计算精确统计（关键！）
    stats = compute_stats()

    # 4. 构造 Prompt
    prompt = f"""你是一个企业数据分析助手。请**直接回答**用户的问题。

【精确统计数据】（优先使用这里的数字！）
{stats}

【相关知识片段】
{rag_context}

【历史对话】
{memory_context if memory_context else "（无）"}

【当前子任务】
{current}

【要求】
- **优先使用"精确统计数据"里的数字**，不要自己算
- 直接给出具体结论和数字，不要罗列"统计维度"
- 用简洁的中文回答，200字以内
- 如果数据中确实没有相关信息，明确说明"""

    result = llm.invoke(prompt)
    print(f"[Skill] 当前子任务：{current}")
    print(f"[Skill] 检索到 {len(docs)} 个片段")

    # 5. 更新短期记忆
    update_session(session_id, "last_interaction", result.content[:500])

    return {
        "current_task": current,
        "retrieved_docs": rag_context[:500],
        "tool_result": result.content,
        "memory_context": memory_context,
        "status": "skill_executed"
    }