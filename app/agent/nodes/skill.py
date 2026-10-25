"""Skill 节点：RAG 检索 + LLM 分析，遍历 Planner 拆解的所有子任务并聚合结果。"""
import logging
import os
from functools import lru_cache

import pandas as pd

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import asyncio

from app.agent.llm import safe_ainvoke
from app.agent.state import AgentState
from app.client.short_term import get_session, update_session
from app.client.vectorstore import similarity_search
from app.conf import config
from app.repository.long_term import get_long_term
from app.service.permission import rag_scope_filter, scoped_region

logger = logging.getLogger(__name__)

# 检索一律走 app.client.vectorstore.similarity_search 统一入口——
# 该入口支持 metadata_filter（Qdrant 原生过滤 / FAISS 召回后过滤），
# 是 RBAC 在"知识检索"这条数据出口上的执行点。


@lru_cache(maxsize=8)
def compute_stats(region: str | None = None) -> str:
    """用 pandas 精确计算销售数据的统计信息（结果缓存，数据不变时只算一次）。

    包含：总量、分产品、分区域、区域×产品交叉、月度趋势、Q1 环比 Q4。
    region 非空时只统计该区域（RBAC 行级隔离的数据出口之一），
    区域受限用户既看不到其他区域明细，也看不到跨区域的汇总值。
    """
    try:
        df_q1 = pd.read_csv(os.path.join(config.DATA_DIR, "sales_q1.csv"))
        df_q1["日期"] = pd.to_datetime(df_q1["日期"])
        df_q1["月份"] = df_q1["日期"].dt.strftime("%Y-%m")
        if region:
            df_q1 = df_q1[df_q1["区域"] == region]
        if df_q1.empty:
            return ""

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
            if region and "区域" in df_q4.columns:
                df_q4 = df_q4[df_q4["区域"] == region]
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


async def skill_node(state: AgentState) -> dict:
    """异步 Skill：所有子任务的 LLM 分析通过 asyncio.gather 并发执行。

    旧版串行：N 个子任务 = N × LLM 延迟；并发后 ≈ 1 × LLM 延迟（最慢那个）。
    检索（本地 FAISS）与统计（lru_cache）本身是微秒级，不值得异步化；
    真正阻塞的是 LLM 网络调用，所以只对 LLM 部分做 gather。
    """
    plan = state.get("plan") or [state["user_input"]]
    session_id = state.get("session_id", "default_session")
    user_id = state.get("user_id", "default_user")

    # 1. 短期记忆（当前会话上下文）
    session_data = get_session(session_id)
    memory_context = session_data.get("last_interaction", "")

    # 2. 长期记忆（跨会话历史）
    long_term_context = _build_long_term_context(user_id)

    # 3. RBAC 数据作用域（与 SQL 层共用 app.service.permission 的同一份权限模型）
    role, region = state.get("user_role", ""), state.get("user_region", "")
    region_scope = scoped_region(role, region)
    scope_filter = rag_scope_filter(role, region)

    # 4. 预计算精确统计（已缓存）：区域受限用户只算本区域，且不注入公司级反馈统计
    stats = compute_stats(region_scope)
    feedback_stats = "" if region_scope else compute_feedback_stats()

    # 5. 逐子任务准备 prompt（同步、快），LLM 调用并发（异步、慢）
    prompts = []
    doc_counts = []
    last_docs_preview = ""
    for task in plan:
        # 检索走统一入口并带上权限过滤：region 受限角色不会命中的公司敏感文档
        docs = similarity_search(task, k=config.RAG_TOP_K, metadata_filter=scope_filter)
        doc_counts.append(len(docs))
        rag_context = "\n\n".join([d.page_content for d in docs])
        last_docs_preview = rag_context[:500]

        prompts.append(f"""你是一个企业数据分析助手。请**直接回答**用户的问题。

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

【输出要求】
- **只引用数字，不要原样复制"精确统计数据"整块内容**，更不要输出"区域×产品交叉表"
- 直接给出具体结论和数字，用 **加粗** 标重点数字
- 结构清晰：先给结论，再用 - 列表补充关键维度（产品/区域/趋势）
- 用简洁的中文回答，300 字以内
- 如果数据中确实没有相关信息，明确说明""")

    results = await asyncio.gather(
        *(
            safe_ainvoke(prompt, fallback="（该子任务处理失败，请稍后重试）")
            for prompt in prompts
        )
    )

    sub_answers = []
    for task, result, n_docs in zip(plan, results, doc_counts):
        sub_answers.append(f"【{task}】\n{result}")
        logger.info("[Skill] 子任务完成：%s（检索到 %d 个片段）", task, n_docs)

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
