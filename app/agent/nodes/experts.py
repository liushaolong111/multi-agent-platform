"""多智能体专家协作节点：成本 / 安全 / 材料三个领域专家并行分析。

设计要点：
- 克制版多智能体：不是全自主群聊，而是"一轮并行分析 + 协调者聚合"，可控可演示
- 每个专家有独立角色 prompt，只看自己领域的统计数据，从专业视角补充 Skill 的初步分析
- 三个专家用 asyncio.gather 并发调用 LLM，延迟 ≈ 1 × 单次 LLM 调用
- 专家分析不取代 Skill 结果，而是提供专业视角的深化与跨域关联建议
"""
import asyncio
import logging

from app.agent.llm import safe_ainvoke
from app.agent.nodes.skill import (
    compute_material_stats,
    compute_safety_stats,
    compute_stats,
)
from app.service.permission import scoped_region

logger = logging.getLogger(__name__)


# ---- 各专家的角色 prompt ----

_COST_EXPERT_PROMPT = """你是工程项目成本管理专家，擅长从预算执行、成本偏差、资源配置角度分析项目。

【用户问题】
{question}

【初步分析结果】（来自通用分析）
{preliminary}

【成本领域精确统计】
{stats}

请从成本管理专业视角，针对用户问题给出补充分析：
- 重点解读预算执行率背后的含义（哪些项目超支、超支程度、可能原因）
- 指出成本结构中值得关注的异常（某类别占比畸高、月度波动异常等）
- 如果存在成本预警（黄/橙/红），说明其业务影响
- 给出成本管控的专业建议

要求：200 字以内，只讲成本视角，不要重复初步分析的内容。"""


_SAFETY_EXPERT_PROMPT = """你是工程项目质量安全管理专家，擅长从隐患排查、整改闭环、风险分级角度分析项目。

【用户问题】
{question}

【初步分析结果】（来自通用分析）
{preliminary}

【质量安全领域精确统计】
{stats}

请从质量安全专业视角，针对用户问题给出补充分析：
- 重点解读未闭环隐患的风险等级（重大/较大/一般）和超期情况
- 指出安全与质量问题的分布特征（哪些区域/项目隐患集中）
- 如果存在安全预警（红/橙/黄），说明其紧迫性和处置优先级
- 给出安全管理的专业建议

要求：200 字以内，只讲安全视角，不要重复初步分析的内容。"""


_MATERIAL_EXPERT_PROMPT = """你是工程项目材料与供应链管理专家，擅长从库存水平、到货滞后、供应商管理角度分析项目。

【用户问题】
{question}

【初步分析结果】（来自通用分析）
{preliminary}

【材料领域精确统计】
{stats}

请从材料供应链专业视角，针对用户问题给出补充分析：
- 重点解读到货滞后对工期的影响（滞后天数、涉及项目和材料）
- 指出材料类别分布和库存水平的合理性
- 如果存在材料滞后预警（红/橙/黄），说明对施工进度的影响程度
- 给出供应链调度的专业建议（如催货、替代供应商、调整施工顺序）

要求：200 字以内，只讲材料视角，不要重复初步分析的内容。"""


async def _cost_expert(question: str, preliminary: str, stats: str) -> str:
    if not stats:
        return ""
    return await safe_ainvoke(
        _COST_EXPERT_PROMPT.format(question=question, preliminary=preliminary, stats=stats),
        fallback="",
    )


async def _safety_expert(question: str, preliminary: str, stats: str) -> str:
    if not stats:
        return ""
    return await safe_ainvoke(
        _SAFETY_EXPERT_PROMPT.format(question=question, preliminary=preliminary, stats=stats),
        fallback="",
    )


async def _material_expert(question: str, preliminary: str, stats: str) -> str:
    if not stats:
        return ""
    return await safe_ainvoke(
        _MATERIAL_EXPERT_PROMPT.format(question=question, preliminary=preliminary, stats=stats),
        fallback="",
    )


async def experts_node(state) -> dict:
    """多智能体专家协作节点：三个领域专家并行分析，结果写入 expert_analyses。

    三个专家用 asyncio.gather 并发执行，延迟 ≈ 最慢那个专家的 LLM 调用。
    统计数据按 RBAC 区域作用域过滤，与 Skill 节点共用同一份权限判定。
    """
    question = state.get("user_input", "")
    preliminary = state.get("tool_result", "")
    role, region = state.get("user_role", ""), state.get("user_region", "")
    region_scope = scoped_region(role, region)

    cost_stats = compute_stats(region_scope)
    safety_stats = compute_safety_stats(region_scope)
    material_stats = compute_material_stats(region_scope)

    cost_analysis, safety_analysis, material_analysis = await asyncio.gather(
        _cost_expert(question, preliminary, cost_stats),
        _safety_expert(question, preliminary, safety_stats),
        _material_expert(question, preliminary, material_stats),
    )

    logger.info(
        "[Experts] 三专家分析完成：cost=%d 字, safety=%d 字, material=%d 字",
        len(cost_analysis), len(safety_analysis), len(material_analysis),
    )

    return {
        "expert_analyses": {
            "cost": cost_analysis,
            "safety": safety_analysis,
            "material": material_analysis,
        }
    }
