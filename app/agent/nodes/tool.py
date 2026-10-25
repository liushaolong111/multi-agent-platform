"""工具注册表：所有 @tool 定义 + 确定性图表生成。

节点逻辑已拆分（Phase 3 ReAct 改造）：
- 决策：app/agent/nodes/tool_decide.py（异步，LLM Function Calling）
- 执行：app/agent/nodes/tool_exec.py（执行 + 观测累积 + 循环回边）
- 审批：app/agent/nodes/approval.py（高风险调用人工确认）
"""
import logging
import os

import pandas as pd
from langchain_core.tools import tool

from app.agent.nodes.skill import compute_feedback_stats, compute_stats
from app.agent.nodes.text2sql import current_user, query_business_database
from app.conf import config
from app.service.permission import scoped_region

logger = logging.getLogger(__name__)


# ---- RBAC 工具出口：所有工具共用同一套数据作用域判定 ----


def _current_region() -> str | None:
    """当前请求用户的数据作用域区域（None = 不限制）。

    与 SQL 层共用 app.service.permission.scoped_region，保证所有数据出口同一套权限判定。
    """
    user = current_user()
    return scoped_region(user.get("role", ""), user.get("region", ""))


def _is_company_sensitive(filename: str) -> bool:
    """文件名是否命中公司级敏感关键词（与 RAG visibility 判定同一份常量）。"""
    stem = filename.rsplit(".", 1)[0].lower()
    return any(kw in stem for kw in config.COMPANY_SENSITIVE_KEYWORDS)


# ---- 工具定义（@tool 装饰器即 JSON Schema，供 LLM 原生 Function Calling 使用）----


@tool
def query_database() -> str:
    """查询销售数据库，返回精确统计数据（销售额、销量、分产品/区域汇总、环比）。"""
    return compute_stats(_current_region()) or "（当前数据权限范围内无销售数据）"


@tool
def query_feedback() -> str:
    """查询客户反馈量化统计（平均评分、好评率、问题类型分布）。"""
    if _current_region():
        return "（客户反馈为公司级数据，当前角色的数据权限不支持查询）"
    return compute_feedback_stats()


@tool
def read_file(filename: str) -> str:
    """读取知识库 data/ 目录下的文件内容。

    Args:
        filename: 文件名，如 product_info.md
    """
    name = os.path.basename(filename)
    if _current_region() and _is_company_sensitive(name):
        return f"（{name} 属公司级数据，当前角色的数据权限不支持读取）"
    filepath = os.path.join(config.DATA_DIR, name)
    if not os.path.exists(filepath):
        return f"文件不存在：{filename}"
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()[:2000]
    except Exception as e:
        return f"读取文件失败：{e}"


_TOOLS = [query_business_database, query_database, query_feedback, read_file]
_TOOL_MAP = {t.name: t for t in _TOOLS}

# 高风险工具：APPROVAL_MODE=true 时执行前需人工审批
_HIGH_RISK_TOOLS = {"query_business_database"}

# 图表关键词：命中任一即确定性触发绘图，不走 LLM 决策（保证必触发）
_CHART_KEYWORDS = ["图", "折线", "趋势", "可视化", "绘制", "画图", "走势", "曲线"]


def needs_chart(plan: list, current_task: str) -> bool:
    """判断当前任务是否要求绘图（确定性前置检查）。"""
    return any(kw in t for t in plan for kw in _CHART_KEYWORDS) or any(
        kw in current_task for kw in _CHART_KEYWORDS
    )


def pending_high_risk_calls(pending: list) -> list:
    """从待执行调用中筛出高风险项（需人工审批的那些）。"""
    return [c for c in pending if c.get("name") in _HIGH_RISK_TOOLS]


def _draw_chart(region: str | None = None) -> str:
    """生成 Q1 月度销售额趋势折线图（SVG，无外部依赖）。

    region 非空时只统计该区域（RBAC：区域受限用户不应看到全公司趋势）。
    """
    try:
        df = pd.read_csv(os.path.join(config.DATA_DIR, "sales_q1.csv"))
        df["日期"] = pd.to_datetime(df["日期"])
        df["月份"] = df["日期"].dt.strftime("%Y-%m")
        if region:
            df = df[df["区域"] == region]
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

        chart_title = f"2026 Q1 {region}月度销售额趋势" if region else "2026 Q1 月度销售额趋势"
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" style="max-width:100%;height:auto;background:#fff;border-radius:8px;font-family:Arial,'Microsoft YaHei',sans-serif">
  <text x="{W/2}" y="28" text-anchor="middle" font-size="15" fill="#1d2129" font-weight="600">{chart_title}</text>
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
