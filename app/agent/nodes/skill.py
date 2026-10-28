"""Skill 节点：RAG 检索 + LLM 分析，遍历 Planner 拆解的所有子任务并聚合结果。"""
import logging
import os

import pandas as pd

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import asyncio

from app.agent.llm import safe_ainvoke
from app.agent.state import AgentState
from app.client.cache import cached
from app.client.short_term import get_session, update_session
from app.client.vectorstore import multi_route_search
from app.conf import config
from app.repository.long_term import get_long_term
from app.service.permission import rag_scope_filter, scoped_region

logger = logging.getLogger(__name__)

# ---- 预警分级阈值：三域共用同一套口径，改这里即全局生效（PRD / README / UI 保持一致）----
# 成本：预算执行率 ≥115% 红 / ≥110% 橙 / ≥105% 黄
COST_ALERT_LEVELS = ((115, "🔴红色预警"), (110, "🟠橙色预警"), (105, "🟡黄色预警"))
# 材料：到货滞后天数 >10 红 / 5-10 橙 / 1-5 黄
MATERIAL_LAG_RED_DAYS, MATERIAL_LAG_ORANGE_DAYS = 10, 5
# 安全：一般隐患未闭环超过 30 天转黄
SAFETY_OVERDUE_DAYS = 30


def _cost_alert_level(rate: float) -> str:
    """按执行率返回成本预警等级标签，未超阈值返回 ✅正常。"""
    for threshold, label in COST_ALERT_LEVELS:
        if rate >= threshold:
            return label
    return "✅正常"

# 检索一律走 app.client.vectorstore.similarity_search 统一入口——
# 该入口支持 metadata_filter（Qdrant 原生过滤 / FAISS 召回后过滤），
# 是 RBAC 在"知识检索"这条数据出口上的执行点。


@cached("stats:cost", ttl=300)
def compute_stats(region: str | None = None) -> str:
    """用 pandas 精确计算项目成本的统计信息（结果缓存，数据不变时只算一次）。

    包含：总成本、预算执行率、分区域、分成本类别、分项目、月度趋势。
    region 非空时只统计该区域（RBAC 行级隔离的数据出口之一），
    区域受限用户既看不到其他区域明细，也看不到跨区域的汇总值。
    """
    try:
        df = pd.read_csv(os.path.join(config.DATA_DIR, "cost_records.csv"))
        if region:
            df = df[df["区域"] == region]
        if df.empty:
            return ""

        total_cost = int(df["金额"].sum())
        total_budget = int(df["预算金额"].sum())
        rate = total_cost / total_budget * 100 if total_budget else 0

        by_region = df.groupby("区域")["金额"].sum().sort_values(ascending=False)
        by_category = df.groupby("成本类别")["金额"].sum().sort_values(ascending=False)
        by_project = df.groupby("项目名称")["金额"].sum().sort_values(ascending=False)
        monthly = df.groupby("成本月份")["金额"].sum().sort_index()

        cross = df.pivot_table(
            index="区域", columns="成本类别", values="金额", aggfunc="sum", fill_value=0
        )

        stats = f"""【精确成本统计（由程序计算，可信）】
实际成本合计：{total_cost:,} 元
预算合计：{total_budget:,} 元
预算执行率：{rate:.2f}%
成本记录数：{len(df)} 条

分区域成本（降序）：
"""
        for name, amount in by_region.items():
            pct = amount / total_cost * 100
            stats += f"  - {name}：{int(amount):,} 元（占比 {pct:.1f}%）\n"

        stats += "\n分成本类别（降序）：\n"
        for name, amount in by_category.items():
            pct = amount / total_cost * 100
            stats += f"  - {name}：{int(amount):,} 元（占比 {pct:.1f}%）\n"

        stats += "\n分项目成本与预算执行率（降序）：\n"
        for name, amount in by_project.items():
            pct = amount / total_cost * 100
            proj_budget = int(df[df["项目名称"] == name]["预算金额"].sum())
            proj_rate = amount / proj_budget * 100 if proj_budget else 0
            # 预警分级：超预算5%黄、10%橙、15%红（阈值见文件顶部 COST_ALERT_LEVELS）
            alert = _cost_alert_level(proj_rate)
            stats += f"  - {name}：{int(amount):,} 元（占比 {pct:.1f}%，执行率 {proj_rate:.1f}% {alert}）\n"

        stats += "\n月度成本趋势：\n"
        for month, amount in monthly.items():
            stats += f"  - {month}：{int(amount):,} 元\n"

        stats += "\n区域×成本类别交叉表（元）：\n"
        stats += "          " + "  ".join(f"{p:>8}" for p in cross.columns) + "\n"
        for region_name in cross.index:
            row = "  ".join(f"{int(cross.loc[region_name, p]):>8}" for p in cross.columns)
            stats += f"  {region_name:<6} {row}\n"

        return stats
    except Exception as e:
        logger.warning("[Skill] 成本统计计算失败：%s", e)
        return ""


@cached("stats:safety", ttl=300)
def compute_safety_stats(region: str | None = None) -> str:
    """用 pandas 精确计算质量安全问题统计（区域受限角色只算本区域）。"""
    try:
        df = pd.read_csv(os.path.join(config.DATA_DIR, "safety_issues.csv"))
        if region:
            df = df[df["区域"] == region]
        if df.empty:
            return ""

        total = len(df)
        closed = int((df["状态"] == "已闭环").sum())
        rate = closed / total * 100 if total else 0
        by_type = df.groupby("问题类型").size().sort_values(ascending=False)
        by_severity = df.groupby("严重程度").size().sort_values(ascending=False)
        by_status = df.groupby("状态").size().sort_values(ascending=False)
        by_region = df.groupby("区域").size().sort_values(ascending=False)

        result = f"""【质量安全问题精确统计（由程序计算，可信）】
统计范围：{region if region else "全部区域"}
问题总数：{total} 条
已闭环：{closed} 条
闭环率：{rate:.2f}%

按问题类型：
"""
        for name, count in by_type.items():
            result += f"  - {name}：{count} 条\n"
        result += "\n按严重程度：\n"
        for name, count in by_severity.items():
            result += f"  - {name}：{count} 条\n"
        result += "\n按整改状态：\n"
        for name, count in by_status.items():
            result += f"  - {name}：{count} 条\n"
        if not region:
            result += "\n按区域分布：\n"
            for name, count in by_region.items():
                result += f"  - {name}：{count} 条\n"

        # 预警分级：重大+未闭环=红，较大+未闭环=橙，一般+超30天未闭环=黄
        from datetime import date as _date
        today = _date(2026, 7, 1)
        open_issues = df[df["状态"] != "已闭环"].copy()
        if not open_issues.empty:
            red = open_issues[open_issues["严重程度"] == "重大"]
            orange = open_issues[open_issues["严重程度"] == "较大"]
            open_issues["发现日期"] = pd.to_datetime(open_issues["发现日期"]).dt.date
            yellow = open_issues[
                (open_issues["严重程度"] == "一般")
                & ((today - open_issues["发现日期"]).apply(lambda x: x.days) > SAFETY_OVERDUE_DAYS)
            ]
            result += f"\n预警汇总：🔴重大未闭环 {len(red)} 条 | 🟠较大未闭环 {len(orange)} 条 | 🟡一般超期未闭环 {len(yellow)} 条\n"
        return result
    except Exception as e:
        logger.warning("[Skill] 安全问题统计计算失败：%s", e)
        return ""


@cached("stats:material", ttl=300)
def compute_material_stats(region: str | None = None) -> str:
    """用 pandas 精确计算材料库存与到货滞后预警（区域受限角色只算本区域）。"""
    try:
        path = os.path.join(config.DATA_DIR, "material_records.csv")
        if not os.path.exists(path):
            return ""
        df = pd.read_csv(path)
        if region:
            df = df[df["区域"] == region]
        if df.empty:
            return ""

        df["预计到货日期"] = pd.to_datetime(df["预计到货日期"]).dt.date
        df["计划使用日期"] = pd.to_datetime(df["计划使用日期"]).dt.date
        df["滞后天数"] = (df["预计到货日期"] - df["计划使用日期"]).apply(lambda x: x.days)

        total = len(df)
        lagged = df[df["滞后天数"] > 0]
        by_cat = df.groupby("材料类别").size().sort_values(ascending=False)

        result = f"""【材料库存与到货预警精确统计（由程序计算，可信）】
统计范围：{region if region else "全部区域"}
材料记录数：{total} 条
到货滞后：{len(lagged)} 条
滞后率：{len(lagged) / total * 100:.1f}%

按材料类别分布：
"""
        for name, count in by_cat.items():
            result += f"  - {name}：{count} 条\n"

        # 滞后预警分级：滞后>10天红，5-10天橙，1-5天黄
        if not lagged.empty:
            red = lagged[lagged["滞后天数"] > MATERIAL_LAG_RED_DAYS]
            orange = lagged[
                (lagged["滞后天数"] > MATERIAL_LAG_ORANGE_DAYS)
                & (lagged["滞后天数"] <= MATERIAL_LAG_RED_DAYS)
            ]
            yellow = lagged[lagged["滞后天数"] <= MATERIAL_LAG_ORANGE_DAYS]
            result += f"\n滞后预警：🔴超10天 {len(red)} 条 | 🟠5-10天 {len(orange)} 条 | 🟡1-5天 {len(yellow)} 条\n"

            result += "\n滞后材料明细（按滞后天数降序）：\n"
            for _, row in lagged.sort_values("滞后天数", ascending=False).head(10).iterrows():
                result += f"  - [{row['区域']}] {row['项目名称']} {row['材料名称']}：滞后 {row['滞后天数']} 天（供应商：{row['供应商']}）\n"

        return result
    except Exception as e:
        logger.warning("[Skill] 材料统计计算失败：%s", e)
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
    """异步 Skill：子任务 LLM 分析 + 三域统计均通过 asyncio.gather 并发执行。

    并发点：
    - pandas 统计（CPU 密集）：asyncio.to_thread 放线程池 + gather 并发三域
    - LLM 分析（IO 密集）：gather 并发子任务
    检索（本地 FAISS）本身是微秒级，不值得异步化。
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

    # 4. 预计算精确统计（Redis 缓存 + 线程池异步 + 三域并发）：
    #    pandas 计算是 CPU 密集型同步操作，用 asyncio.to_thread 放到线程池，
    #    三个域的统计用 gather 并发，避免阻塞事件循环
    stats, safety_stats, material_stats = await asyncio.gather(
        asyncio.to_thread(compute_stats, region_scope),
        asyncio.to_thread(compute_safety_stats, region_scope),
        asyncio.to_thread(compute_material_stats, region_scope),
    )

    # 5. 逐子任务准备 prompt（同步、快），LLM 调用并发（异步、慢）
    prompts = []
    doc_counts = []
    last_docs_preview = ""
    for task in plan:
        # 多路召回：分桶路由 + dense/sparse 混合检索 + RRF 融合，并带上 RBAC 权限过滤
        docs = multi_route_search(task, k=config.RAG_TOP_K, metadata_filter=scope_filter)
        doc_counts.append(len(docs))
        rag_context = "\n\n".join([d.page_content for d in docs])
        last_docs_preview = rag_context[:500]

        prompts.append(f"""你是一个工程项目管理数据分析助手。请**直接回答**用户的问题。

【精确成本统计】（优先使用这里的数字！）
{stats}

【质量安全问题精确统计】（涉及安全、质量、整改问题时使用）
{safety_stats}

【材料库存与到货预警精确统计】（涉及材料、库存、到货、供应商时使用）
{material_stats}

【相关知识片段】
{rag_context}

【历史对话（短期）】
{memory_context if memory_context else "（无）"}

【历史交互（长期）】
{long_term_context if long_term_context else "（无）"}

【当前子任务】
{task}

【输出要求】
- **只引用数字，不要原样复制"精确统计数据"整块内容**，更不要输出"区域×成本类别交叉表"
- 直接给出具体结论和数字，用 **加粗** 标重点数字
- 结构清晰：先给结论，再用 - 列表补充关键维度（项目/区域/成本类别/趋势）
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
