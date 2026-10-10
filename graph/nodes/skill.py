"""Skill 节点：RAG 检索 + LLM 分析，遍历 Planner 拆解的所有子任务并聚合结果。"""
import logging
import os
from functools import lru_cache

import pandas as pd

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

import config
from graph.llm import safe_invoke
from graph.state import AgentState
from memory.short_term import get_session, update_session
from memory.long_term import get_long_term

logger = logging.getLogger(__name__)

# 全局加载一次向量库
embeddings = HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL)
vectorstore = FAISS.load_local(
    config.FAISS_INDEX_DIR,
    embeddings,
    allow_dangerous_deserialization=True,
)


@lru_cache(maxsize=1)
def compute_stats() -> str:
    """用 pandas 精确计算销售数据的统计信息（结果缓存，数据不变时只算一次）。"""
    try:
        df = pd.read_csv(os.path.join(config.DATA_DIR, "sales_q1.csv"))

        total_sales = df["销售额"].sum()
        total_qty = df["数量"].sum()

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
        logger.warning("[Skill] 统计计算失败：%s", e)
        return ""


def _build_long_term_context(user_id: str) -> str:
    """从长期记忆中取最近几条交互作为上下文。"""
    records = get_long_term(user_id, limit=3)
    if not records:
        return ""
    parts = []
    for r in records:
        content = r.content if isinstance(r.content, dict) else {}
        user_input = content.get("user_input", "")
        answer = content.get("answer", "")
        if user_input or answer:
            parts.append(f"问：{user_input}\n答：{answer[:200]}")
    return "\n\n".join(parts)


def skill_node(state: AgentState) -> dict:
    plan = state.get("plan") or [state["user_input"]]
    session_id = state.get("session_id", "default_session")
    user_id = state.get("user_id", "default_user")

    # 1. 短期记忆（当前会话上下文）
    session_data = get_session(session_id)
    memory_context = session_data.get("last_interaction", "")

    # 2. 长期记忆（跨会话历史）
    long_term_context = _build_long_term_context(user_id)

    # 3. 预计算精确统计（已缓存）
    stats = compute_stats()

    # 4. 遍历所有子任务，逐个分析后聚合
    sub_answers = []
    last_docs_preview = ""
    for task in plan:
        docs = vectorstore.similarity_search(task, k=config.RAG_TOP_K)
        rag_context = "\n\n".join([d.page_content for d in docs])
        last_docs_preview = rag_context[:500]

        prompt = f"""你是一个企业数据分析助手。请**直接回答**用户的问题。

【精确统计数据】（优先使用这里的数字！）
{stats}

【相关知识片段】
{rag_context}

【历史对话（短期）】
{memory_context if memory_context else "（无）"}

【历史交互（长期）】
{long_term_context if long_term_context else "（无）"}

【当前子任务】
{task}

【要求】
- **优先使用"精确统计数据"里的数字**，不要自己算
- 直接给出具体结论和数字，不要罗列"统计维度"
- 用简洁的中文回答，200字以内
- 如果数据中确实没有相关信息，明确说明"""

        result = safe_invoke(prompt, fallback="（该子任务处理失败，请稍后重试）")
        sub_answers.append(f"【{task}】\n{result}")
        logger.info("[Skill] 子任务完成：%s（检索到 %d 个片段）", task, len(docs))

    # 5. 聚合成最终回答
    final_tool_result = "\n\n".join(sub_answers)

    # 6. 更新短期记忆
    update_session(session_id, "last_interaction", final_tool_result[:500])

    return {
        "current_task": plan[0],
        "retrieved_docs": last_docs_preview,
        "tool_result": final_tool_result,
        "memory_context": memory_context,
        "status": "skill_executed",
    }
