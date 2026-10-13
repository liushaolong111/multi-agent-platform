"""Tool 节点：通过 LLM 原生 Function Calling 决策并执行外部工具调用。"""
import logging
import os

import pandas as pd
from langchain_core.tools import tool

import config
from graph.llm import safe_invoke_with_tools
from graph.metrics import metrics
from graph.nodes.skill import compute_feedback_stats, compute_stats
from graph.state import AgentState

logger = logging.getLogger(__name__)


# ---- 工具定义（@tool 装饰器即 JSON Schema，供 LLM 原生 Function Calling 使用）----


@tool
def query_database() -> str:
    """查询销售数据库，返回精确统计数据（销售额、销量、分产品/区域汇总、环比）。"""
    return compute_stats()


@tool
def query_feedback() -> str:
    """查询客户反馈量化统计（平均评分、好评率、问题类型分布）。"""
    return compute_feedback_stats()


@tool
def read_file(filename: str) -> str:
    """读取知识库 data/ 目录下的文件内容。

    Args:
        filename: 文件名，如 product_info.md
    """
    filepath = os.path.join(config.DATA_DIR, os.path.basename(filename))
    if not os.path.exists(filepath):
        return f"文件不存在：{filename}"
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()[:2000]
    except Exception as e:
        return f"读取文件失败：{e}"


_TOOLS = [query_database, query_feedback, read_file]
_TOOL_MAP = {t.name: t for t in _TOOLS}


def _draw_chart() -> str:
    """生成 Q1 月度销售额趋势折线图（SVG，无外部依赖）。"""
    try:
        df = pd.read_csv(os.path.join(config.DATA_DIR, "sales_q1.csv"))
        df["日期"] = pd.to_datetime(df["日期"])
        df["月份"] = df["日期"].dt.strftime("%Y-%m")
        monthly = df.groupby("月份")["销售额"].sum().sort_index()
        months = list(monthly.index)
        values = [int(v) for v in monthly.values]
        wan = [v / 10000 for v in values]  # 转万元

        # ---- 画布参数 ----
        W, H = 640, 340
        pad_l, pad_r, pad_t, pad_b = 70, 30, 50, 60
        plot_w = W - pad_l - pad_r
        plot_h = H - pad_t - pad_b

        vmin = min(wan)
        vmax = max(wan)
        vrange = vmax - vmin if vmax > vmin else 1
        # Y 轴上下留 10% 余量
        y_min = vmin - vrange * 0.15
        y_max = vmax + vrange * 0.15
        y_range = y_max - y_min

        n = len(months)
        if n < 2:
            return "数据点不足，无法绘制折线图"

        # X 坐标
        xs = [pad_l + plot_w * i / (n - 1) for i in range(n)]
        # Y 坐标（SVG 坐标从上往下，所以反转）
        ys = [pad_t + plot_h * (1 - (v - y_min) / y_range) for v in wan]

        # 折线 path
        line_path = " ".join(
            f"{'M' if i == 0 else 'L'}{xs[i]:.1f},{ys[i]:.1f}" for i in range(n)
        )
        # 填充区域
        area_path = (
            f"M{xs[0]:.1f},{pad_t + plot_h:.1f} "
            + " ".join(f"L{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
            + f" L{xs[-1]:.1f},{pad_t + plot_h:.1f} Z"
        )

        # Y 轴刻度（5 条）
        y_ticks = ""
        for i in range(5):
            val = y_min + y_range * i / 4
            y = pad_t + plot_h * (1 - i / 4)
            y_ticks += (
                f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}" stroke="#e4e6eb" stroke-width="1"/>'
                f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#8a8f99">{val:.0f}</text>'
            )

        # X 轴刻度
        x_labels = ""
        for i, m in enumerate(months):
            x_labels += (
                f'<text x="{xs[i]:.1f}" y="{H - pad_b + 20}" text-anchor="middle" font-size="12" fill="#4e5969">{m}</text>'
            )

        # 数据点 + 值标签
        points = ""
        for i, (x, y, v) in enumerate(zip(xs, ys, wan)):
            points += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#4e83fd" stroke="#fff" stroke-width="2"/>'
            points += f'<text x="{x:.1f}" y="{y - 12:.1f}" text-anchor="middle" font-size="11" fill="#1d2129" font-weight="600">{v:.1f}万</text>'

        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" style="max-width:100%;height:auto;background:#fff;border-radius:8px;font-family:Arial,'Microsoft YaHei',sans-serif">
  <text x="{W/2}" y="28" text-anchor="middle" font-size="15" fill="#1d2129" font-weight="600">2026 Q1 月度销售额趋势</text>
  <line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" stroke="#c9cdd4" stroke-width="1"/>
  <line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{W - pad_r}" y2="{pad_t + plot_h}" stroke="#c9cdd4" stroke-width="1"/>
  {y_ticks}
  {x_labels}
  <path d="{area_path}" fill="#4e83fd" fill-opacity="0.08" stroke="none"/>
  <path d="{line_path}" fill="none" stroke="#4e83fd" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
  {points}
  <text x="{pad_l - 50}" y="{pad_t - 10}" font-size="11" fill="#8a8f99">销售额(万元)</text>
</svg>'''

        # 用特殊标记包裹，前端 renderMarkdown 识别后直接渲染 SVG
        return f"[[CHART_BEGIN]]{svg}[[CHART_END]]"
    except Exception as e:
        logger.warning("[Tool] draw_chart 失败：%s", e)
        return f"绘图失败：{e}"


def tool_node(state: AgentState) -> dict:
    """决策并执行工具调用，把结果追加到 tool_result 供 Reviewer 参考。"""
    current_task = state.get("current_task", "")
    plan = state.get("plan") or []
    all_tasks = "；".join(plan) if plan else current_task
    existing_result = state.get("tool_result", "")

    # 确定性前置检查：任一子任务含图表关键词，直接绘图（不走 LLM，保证必触发）
    chart_keywords = ["图", "折线", "趋势", "可视化", "绘制", "画图", "走势", "曲线"]
    if any(kw in t for t in plan for kw in chart_keywords) or any(
        kw in current_task for kw in chart_keywords
    ):
        tool_output = _draw_chart()
        metrics.record_tool("draw_chart")
        logger.info("[Tool] 命中图表关键词，直接调用 draw_chart")
        merged = f"{existing_result}\n\n【工具补充数据】\n{tool_output}"
        return {"tool_result": merged, "status": "tool_processed"}

    prompt = f"""根据以下信息，判断是否需要调用工具来补充数据。
已有回答完整、无需补充时可以不调用任何工具。

全部子任务：{all_tasks}
已有回答：{existing_result[:200]}"""

    response = safe_invoke_with_tools(prompt, _TOOLS)

    tool_outputs = []
    if response is not None:
        for call in response.tool_calls:
            tool_fn = _TOOL_MAP.get(call["name"])
            if tool_fn is None:
                logger.warning("[Tool] LLM 请求了未注册的工具：%s", call["name"])
                continue
            tool_outputs.append(tool_fn.invoke(call["args"]))
            metrics.record_tool(call["name"])
            logger.info("[Tool] 调用工具 %s 成功，参数：%s", call["name"], call["args"])
    else:
        logger.warning("[Tool] 工具决策失败，跳过工具调用")

    # 把工具结果追加到已有回答后面，让 Reviewer 能看到补充数据
    if tool_outputs:
        merged = existing_result + "\n\n【工具补充数据】\n" + "\n\n".join(tool_outputs)
    else:
        merged = existing_result

    return {"tool_result": merged, "status": "tool_processed"}
