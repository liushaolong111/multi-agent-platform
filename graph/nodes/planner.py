"""Planner 节点：将任务拆解为 2-4 个可执行子任务。"""
import json
import logging

from graph.llm import safe_invoke
from graph.state import AgentState

logger = logging.getLogger(__name__)


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

    raw = safe_invoke(prompt, fallback="[]")
    content = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        plan = json.loads(content)
        if not isinstance(plan, list) or not plan:
            raise ValueError("plan 不是非空列表")
    except Exception:
        logger.warning("[Planner] JSON 解析失败，回退为单任务。原始输出：%s", raw[:200])
        plan = [state['user_input']]

    logger.info("[Planner] 规划结果：%s", plan)
    return {
        "plan": plan,
        "status": "planned",
        "iteration": state.get("iteration", 0) + 1,
    }
