"""业务库 → RAG 知识库 定时同步脚本。

把业务库的最新数据生成摘要文档写入 data/，再触发向量索引重建，
让 RAG 检索到的知识永远来自业务库的最新状态，而不是静态手工文件。

用法（手动执行）：
    .venv/Scripts/python.exe app/script/sync_business_to_rag.py

生产部署（定时）：
    - Windows：任务计划程序，每天 02:00 跑本脚本
    - Linux：crontab `0 2 * * * cd /app && python app/script/sync_business_to_rag.py`
    - Docker：单独 scheduler 容器循环 sleep + 执行

注意：摘要文件名带 sync 关键字，入库时会被标记为 visibility=company，
区域经理（manager）检索不到——这是刻意的 RBAC 设计，避免通过摘要绕过区域隔离。
"""
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from sqlalchemy import func

from app.conf import config
from app.model.business import CostRecord, Project, SafetyIssue
from app.repository.business import get_business_session


def generate_cost_summary(session) -> str:
    """从业务库生成项目成本摘要 Markdown（集团全量口径）。"""
    total_cost, total_budget = session.query(
        func.sum(CostRecord.amount), func.sum(CostRecord.budget_amount)
    ).one()
    total_cost, total_budget = total_cost or 0, total_budget or 0
    rate = total_cost / total_budget * 100 if total_budget else 0

    by_region = (
        session.query(CostRecord.region, func.sum(CostRecord.amount))
        .group_by(CostRecord.region)
        .order_by(func.sum(CostRecord.amount).desc())
        .all()
    )
    by_category = (
        session.query(CostRecord.cost_category, func.sum(CostRecord.amount))
        .group_by(CostRecord.cost_category)
        .order_by(func.sum(CostRecord.amount).desc())
        .all()
    )
    by_project = (
        session.query(CostRecord.project_name, func.sum(CostRecord.amount))
        .group_by(CostRecord.project_name)
        .order_by(func.sum(CostRecord.amount).desc())
        .all()
    )
    status_counts = dict(
        session.query(Project.status, func.count(Project.id)).group_by(Project.status).all()
    )

    lines = [
        "# 项目成本摘要（由业务数据库自动同步生成）",
        "",
        f"统计口径：2026 年 1—6 月，覆盖 {session.query(func.count(Project.id)).scalar()} 个项目",
        "",
        f"实际成本合计：{int(total_cost):,} 元；预算合计：{int(total_budget):,} 元；"
        f"整体预算执行率：{rate:.2f}%",
        "",
        "## 分区域成本",
    ]
    for region, amount in by_region:
        lines.append(f"- {region}：{int(amount):,} 元（占比 {amount / total_cost * 100:.1f}%）")
    lines.append("\n## 分成本类别")
    for category, amount in by_category:
        lines.append(f"- {category}：{int(amount):,} 元（占比 {amount / total_cost * 100:.1f}%）")
    lines.append("\n## 分项目成本（降序）")
    for name, amount in by_project:
        lines.append(f"- {name}：{int(amount):,} 元")
    lines.append("\n## 项目状态分布")
    for status, count in status_counts.items():
        lines.append(f"- {status}：{count} 个")
    return "\n".join(lines)


def generate_safety_summary(session) -> str:
    """从业务库生成质量安全问题摘要 Markdown（集团全量口径）。"""
    total = session.query(func.count(SafetyIssue.id)).scalar() or 0
    closed = (
        session.query(func.count(SafetyIssue.id))
        .filter(SafetyIssue.status == "已闭环")
        .scalar()
        or 0
    )
    rate = closed / total * 100 if total else 0

    by_type = (
        session.query(SafetyIssue.issue_type, func.count(SafetyIssue.id))
        .group_by(SafetyIssue.issue_type)
        .all()
    )
    by_severity = (
        session.query(SafetyIssue.severity, func.count(SafetyIssue.id))
        .group_by(SafetyIssue.severity)
        .all()
    )
    by_region = (
        session.query(SafetyIssue.region, func.count(SafetyIssue.id))
        .group_by(SafetyIssue.region)
        .order_by(func.count(SafetyIssue.id).desc())
        .all()
    )

    lines = [
        "# 质量安全问题摘要（由业务数据库自动同步生成）",
        "",
        f"问题总数：{total} 条；已闭环 {closed} 条；闭环率 {rate:.2f}%",
        "",
        "## 按问题类型",
    ]
    for issue_type, count in by_type:
        lines.append(f"- {issue_type}：{count} 条")
    lines.append("\n## 按严重程度")
    for severity, count in by_severity:
        lines.append(f"- {severity}：{count} 条")
    lines.append("\n## 按区域分布")
    for region, count in by_region:
        lines.append(f"- {region}：{count} 条")
    return "\n".join(lines)


def main():
    session = get_business_session()
    if not session:
        print("业务数据库不可用，同步终止。")
        sys.exit(1)

    try:
        cost_md = generate_cost_summary(session)
        safety_md = generate_safety_summary(session)
    finally:
        session.close()

    # 写入 data/（覆盖旧的同步产物，不动手工维护的原始文档）
    os.makedirs(config.DATA_DIR, exist_ok=True)
    cost_path = os.path.join(config.DATA_DIR, "sync_cost_summary.md")
    safety_path = os.path.join(config.DATA_DIR, "sync_safety_summary.md")
    with open(cost_path, "w", encoding="utf-8") as f:
        f.write(cost_md)
    with open(safety_path, "w", encoding="utf-8") as f:
        f.write(safety_md)
    print(f"✅ 摘要已生成：{cost_path}、{safety_path}")

    # 触发向量索引重建，让 RAG 检索到最新数据
    from app.script.ingest import build_index

    n_chunks = build_index()
    print(f"✅ 向量索引已重建：{n_chunks} 个文本块")


if __name__ == "__main__":
    main()
