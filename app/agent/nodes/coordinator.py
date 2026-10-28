"""协调者节点：聚合成本 / 安全 / 材料三个专家的分析，生成综合回答。

设计要点：
- 协调者不是再做一遍分析，而是"整合视角"——把三个专家的专业意见融合成一个连贯的回答
- 优先保留专家分析中的关键数字和预警等级，避免协调者"总结掉"重要信息
- 输出写回 tool_result，让后续 ReAct 循环和 Reviewer 看到的是综合后的答案
- 如果专家分析全部为空（LLM 调用失败），直接透传 Skill 的初步结果，不阻塞主流程
"""
import logging

from app.agent.llm import safe_ainvoke

logger = logging.getLogger(__name__)


_COORDINATOR_PROMPT = """你是工程项目管理分析的协调者，负责整合成本、安全、材料三个领域专家的意见，给出最终回答。

【用户问题】
{question}

【通用初步分析】
{preliminary}

【成本专家分析】
{cost}

【安全专家分析】
{safety}

【材料专家分析】
{material}

请综合以上信息，给出最终回答：
- 先给出核心结论，再分维度补充（成本 / 安全 / 材料）
- 保留各专家提到的关键数字和预警等级（🔴🟠🟡）
- 指出跨域关联（如成本超支可能与材料滞后有关）
- 用简洁中文，400 字以内
- 如果某领域专家没有给出分析，跳过该领域"""


async def coordinator_node(state) -> dict:
    """协调者：聚合三专家分析，输出综合回答，写回 tool_result。"""
    question = state.get("user_input", "")
    preliminary = state.get("tool_result", "")
    analyses = state.get("expert_analyses", {}) or {}

    cost = analyses.get("cost", "")
    safety = analyses.get("safety", "")
    material = analyses.get("material", "")

    # 全部专家分析为空时，直接透传初步结果，避免多一次无意义的 LLM 调用
    if not any([cost, safety, material]):
        logger.info("[Coordinator] 专家分析均为空，透传初步结果")
        return {"coordinated_answer": preliminary}

    answer = await safe_ainvoke(
        _COORDINATOR_PROMPT.format(
            question=question,
            preliminary=preliminary,
            cost=cost or "（无）",
            safety=safety or "（无）",
            material=material or "（无）",
        ),
        fallback=preliminary,
    )

    logger.info("[Coordinator] 综合回答生成完成，%d 字", len(answer))

    # 写回 tool_result，让后续 ReAct / Reviewer 看到综合答案
    return {"coordinated_answer": answer, "tool_result": answer}
