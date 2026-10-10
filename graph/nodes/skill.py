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
    """用 pandas 精确计算销售数据的统计信息（结果缓存，数据不变时只算一次）。

    包含：总量、分产品、分区域、区域×产品交叉、月度趋势、Q1 环比 Q4。
    """
    try:
        df_q1 = pd.read_csv(os.path.join(config.DATA_DIR, "sales_q1.csv"))
        df_q1["日期"] = pd.to_datetime(df_q1["日期"])
        df_q1["月份"] = df_q1["日期"].dt.strftime("%Y-%m")

        total_sales = int(df_q1["销售额"].sum())
        total_qty = int(df_q1["数量"].sum())

        by_product = df_q1.groupby("产品")["销售额"].sum().sort_values(ascending=False)
        by_region = df_q1.groupby("区域")["销售额"].sum().sort_values(ascending=False)

        # 区域 × 产品 交叉表
        cross = df_q1.pivot_table(
            index="区域", columns="产品", values="销售额", aggfunc="sum", fill_value=0
        )

        # 月度趋势
        monthly = df_q1.groupby("月份")["销售额"].sum()

        # Q1 环比 Q4 2025
        qoq_info = ""
        q4_path = os.path.join(config.DATA_DIR, "sales_q4_2025.csv")
        if os.path.exists(q4_path):
            df_q4 = pd.read_csv(q4_path)
            q4_total = int(df_q4["销售额"].sum())
            qoq = (total_sales - q4_total) / q4_total * 100
            qoq_info = (
                f"\n环比（Q1 vs Q4 2025）：\n"
                f"  Q4 2025 总销售额：{q4_total:,} 元\n"
                f"  Q1 2026 总销售额：{total_sales:,} 元\n"
                f"  环比增长率：{qoq:+.1f}%\n"
            )

        stats = f"""【精确统计数据（由程序计算，可信）】
总销售额：{total_sales:,} 元
总销量：{total_qty} 件
记录数：{len(df_q1)} 条

分产品销售额（降序）：
"""
        for product, amount in by_product.items():
            pct = amount / total_sales * 100
            stats += f"  - {product}：{int(amount):,} 元（占比 {pct:.1f}%）\n"

        stats += "\n分区域销售额（降序）：\n"
        for region, amount in by_region.items():
            pct = amount / total_sales * 100
            stats += f"  - {region}：{int(amount):,} 元（占比 {pct:.1f}%）\n"

        stats += "\n区域×产品交叉表（元）：\n"
        stats += "          " + "  ".join(f"{p:>8}" for p in cross.columns) + "\n"
        for region in cross.index:
            row = "  ".join(f"{int(cross.loc[region, p]):>8}" for p in cross.columns)
            stats += f"  {region:<6} {row}\n"

        stats += "\n月度销售额趋势：\n"
        for month, amount in monthly.items():
            stats += f"  - {month}：{int(amount):,} 元\n"

        if qoq_info:
            stats += qoq_info

        return stats
    except Exception as e:
        logger.warning("[Skill] 统计计算失败：%s", e)
        return ""


@lru_cache(maxsize=1)
def compute_feedback_stats() -> str:
    """从 customer_feedback.md 解析反馈表，计算量化统计。"""
    try:
        import re

        path = os.path.join(config.DATA_DIR, "customer_feedback.md")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # 解析 Markdown 表格行：| 编号 | 客户 | 评分 | 问题类型 | 描述 |
        rows = re.findall(
            r"\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*(\d)\s*\|\s*([^|]+?)\s*\|",
            content,
        )
        if not rows:
            return ""

        ratings = [int(r[1]) for r in rows]
        types = [r[2].strip() for r in rows]
        avg_rating = sum(ratings) / len(ratings)

        # 评分分布
        dist = {}
        for r in ratings:
            dist[r] = dist.get(r, 0) + 1

        # 问题类型分布
        type_counts = {}
        type_ratings = {}
        for _, rating, ftype in rows:
            type_counts[ftype] = type_counts.get(ftype, 0) + 1
            type_ratings.setdefault(ftype, []).append(int(rating))

        result = f"""【客户反馈精确统计（由程序计算，可信）】
反馈总数：{len(rows)} 条
平均评分：{avg_rating:.2f} / 5.0
好评率（4-5分）：{(dist.get(5,0)+dist.get(4,0))/len(rows)*100:.1f}%
差评率（2-3分）：{(dist.get(2,0)+dist.get(3,0))/len(rows)*100:.1f}%

评分分布：
"""
        for score in sorted(dist.keys(), reverse=True):
            result += f"  - {score} 分：{dist[score]} 条（{dist[score]/len(rows)*100:.0f}%）\n"

        result += "\n问题类型分布（按反馈数）：\n"
        for ftype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            avg = sum(type_ratings[ftype]) / len(type_ratings[ftype])
            result += f"  - {ftype}：{cnt} 条，平均评分 {avg:.1f}\n"

        return result
    except Exception as e:
        logger.warning("[Skill] 反馈统计计算失败：%s", e)
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
    feedback_stats = compute_feedback_stats()

    # 4. 遍历所有子任务，逐个分析后聚合
    sub_answers = []
    last_docs_preview = ""
    for task in plan:
        docs = vectorstore.similarity_search(task, k=config.RAG_TOP_K)
        rag_context = "\n\n".join([d.page_content for d in docs])
        last_docs_preview = rag_context[:500]

        prompt = f"""你是一个企业数据分析助手。请**直接回答**用户的问题。

【精确销售统计】（优先使用这里的数字！）
{stats}

【客户反馈精确统计】（涉及客户满意度时使用）
{feedback_stats}

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
