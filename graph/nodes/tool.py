"""Tool 节点：根据当前任务决定是否调用外部工具，并执行工具调用。"""
import json
import logging
import os

import config
from graph.llm import safe_invoke
from graph.state import AgentState
from graph.nodes.skill import compute_stats, compute_feedback_stats

logger = logging.getLogger(__name__)


def _tool_query_database() -> str:
    """查询销售数据库，返回精确统计数据。"""
    return compute_stats()


def _tool_query_feedback() -> str:
    """查询客户反馈统计，返回量化的评分与问题类型分布。"""
    return compute_feedback_stats()


def _tool_read_file(filename: str) -> str:
    """读取 data/ 目录下的文件内容。"""
    filepath = os.path.join(config.DATA_DIR, filename)
    if not os.path.exists(filepath):
        return f"文件不存在：{filename}"
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()[:2000]
    except Exception as e:
        return f"读取文件失败：{e}"


_TOOLS = {
    "query_database": _tool_query_database,
    "query_feedback": _tool_query_feedback,
    "read_file": _tool_read_file,
}


def tool_node(state: AgentState) -> dict:
    """判断是否需要调用外部工具，需要则执行并把结果追加到 tool_result。"""
    current_task = state.get("current_task", "")
    existing_result = state.get("tool_result", "")

    prompt = f"""根据以下信息，判断是否需要调用外部工具来辅助回答。

当前任务：{current_task}
已有回答：{existing_result[:200]}

可用工具：
- query_database：查询销售数据库的精确统计数据（销售额、销量、分产品/区域汇总、环比）
- query_feedback：查询客户反馈量化统计（平均评分、问题类型分布）
- read_file：读取知识库文件，参数为文件名（如 product_info.md）

判断规则：
- 如果已有回答已包含具体数字且完整，返回 "no_tool"
- 如果需要精确的销售数字，返回 {{"tool": "query_database"}}
- 如果需要客户满意度/反馈统计，返回 {{"tool": "query_feedback"}}
- 如果需要读取某个文件内容，返回 {{"tool": "read_file", "filename": "xxx"}}
- 只返回 JSON 或 no_tool，不要其他内容"""

    raw = safe_invoke(prompt, fallback="no_tool")
    decision = raw.strip().replace("```json", "").replace("```", "").strip()

    tool_output = ""
    if decision.lower() != "no_tool":
        try:
            parsed = json.loads(decision)
            tool_name = parsed.get("tool")
            if tool_name in _TOOLS:
                if tool_name == "read_file":
                    tool_output = _TOOLS[tool_name](parsed.get("filename", ""))
                else:
                    tool_output = _TOOLS[tool_name]()
                logger.info("[Tool] 调用工具 %s 成功", tool_name)
            else:
                logger.warning("[Tool] 未知工具：%s", tool_name)
        except json.JSONDecodeError:
            logger.warning("[Tool] 工具决策解析失败：%s", decision[:100])

    # 把工具结果追加到已有回答后面，让 Reviewer 能看到补充数据
    if tool_output:
        merged = f"{existing_result}\n\n【工具补充数据】\n{tool_output}"
    else:
        merged = existing_result

    return {"tool_result": merged, "status": "tool_processed"}
