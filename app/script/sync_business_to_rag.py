"""业务库 → RAG 知识库 定时同步脚本。

把业务库的最新数据生成摘要文档写入 data/，再触发向量索引重建，
让 RAG 检索到的知识永远来自业务库的最新状态，而不是静态手工文件。

用法（手动执行）：
    .venv/Scripts/python.exe app/script/sync_business_to_rag.py

生产部署（定时）：
    - Windows：任务计划程序，每天 02:00 跑本脚本
    - Linux：crontab `0 2 * * * cd /app && python app/script/sync_business_to_rag.py`
    - Docker：单独 scheduler 容器循环 sleep + 执行
"""
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.conf import config
from app.model.business import FeedbackRecord, SalesRecord
from app.repository.business import get_business_session


def generate_sales_summary(session) -> str:
    """从业务库生成销售数据摘要 Markdown。"""
    from sqlalchemy import func

    total_amount, total_qty, n_records = session.query(
        func.sum(SalesRecord.amount),
        func.sum(SalesRecord.quantity),
        func.count(SalesRecord.id),
    ).one()

    by_product = (
        session.query(SalesRecord.product, func.sum(SalesRecord.amount))
        .group_by(SalesRecord.product)
        .order_by(func.sum(SalesRecord.amount).desc())
        .all()
    )
    by_region = (
        session.query(SalesRecord.region, func.sum(SalesRecord.amount))
        .group_by(SalesRecord.region)
        .order_by(func.sum(SalesRecord.amount).desc())
        .all()
    )

    lines = [
        "# 销售数据摘要（由业务数据库自动同步生成）",
        "",
        f"总销售额：{int(total_amount or 0):,} 元，总销量：{int(total_qty or 0)} 件，记录数：{n_records} 条",
        "",
        "## 分产品销售额",
    ]
    for product, amount in by_product:
        lines.append(f"- {product}：{int(amount):,} 元")
    lines.append("\n## 分区域销售额")
    for region, amount in by_region:
        lines.append(f"- {region}：{int(amount):,} 元")
    return "\n".join(lines)


def generate_feedback_summary(session) -> str:
    """从业务库生成客户反馈摘要 Markdown。"""
    from sqlalchemy import func

    n = session.query(func.count(FeedbackRecord.id)).scalar()
    avg = session.query(func.avg(FeedbackRecord.rating)).scalar() or 0
    by_type = (
        session.query(
            FeedbackRecord.issue_type,
            func.count(FeedbackRecord.id),
            func.avg(FeedbackRecord.rating),
        )
        .group_by(FeedbackRecord.issue_type)
        .order_by(func.count(FeedbackRecord.id).desc())
        .all()
    )

    lines = [
        "# 客户反馈摘要（由业务数据库自动同步生成）",
        "",
        f"反馈总数：{n} 条，平均评分：{avg:.2f} / 5.0",
        "",
        "## 问题类型分布",
    ]
    for issue_type, count, avg_rating in by_type:
        lines.append(f"- {issue_type}：{count} 条，平均评分 {avg_rating:.1f}")
    return "\n".join(lines)


def main():
    session = get_business_session()
    if not session:
        print("业务数据库不可用，同步终止。")
        sys.exit(1)

    try:
        sales_md = generate_sales_summary(session)
        feedback_md = generate_feedback_summary(session)
    finally:
        session.close()

    # 写入 data/（覆盖旧的同步产物，不动手工维护的原始文档）
    os.makedirs(config.DATA_DIR, exist_ok=True)
    sales_path = os.path.join(config.DATA_DIR, "sync_sales_summary.md")
    feedback_path = os.path.join(config.DATA_DIR, "sync_feedback_summary.md")
    with open(sales_path, "w", encoding="utf-8") as f:
        f.write(sales_md)
    with open(feedback_path, "w", encoding="utf-8") as f:
        f.write(feedback_md)
    print(f"✅ 摘要已生成：{sales_path}、{feedback_path}")

    # 触发向量索引重建，让 RAG 检索到最新数据
    from app.script.ingest import build_index

    n_chunks = build_index()
    print(f"✅ 向量索引已重建：{n_chunks} 个文本块")


if __name__ == "__main__":
    main()
