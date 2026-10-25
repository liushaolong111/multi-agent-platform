"""业务库种子脚本：把 data/ 下的 CSV / Markdown 数据灌入业务数据库。

用法：
    .venv/Scripts/python.exe app/script/seed_business_db.py

幂等：每次运行先清空表再插入，可重复执行。
真实业务场景中这一步对应"从 ERP/CRM 定时同步"，此处用项目自带 demo 数据模拟。
"""
import os
import re
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.conf import config
from app.model.business import FeedbackRecord, SalesRecord
from app.repository.business import business_engine, get_business_session


def seed_sales(session) -> int:
    """从 sales_q1.csv + sales_q4_2025.csv 灌入销售流水。"""
    count = 0
    for filename in ("sales_q1.csv", "sales_q4_2025.csv"):
        path = os.path.join(config.DATA_DIR, filename)
        if not os.path.exists(path):
            print(f"  跳过（文件不存在）：{filename}")
            continue
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            session.add(
                SalesRecord(
                    sale_date=pd.to_datetime(row["日期"]).date(),
                    product=str(row["产品"]),
                    region=str(row["区域"]),
                    amount=float(row["销售额"]),
                    quantity=int(row["数量"]),
                )
            )
            count += 1
    return count


def seed_feedback(session) -> int:
    """从 customer_feedback.md 解析反馈表灌库。

    表结构：| 编号 | 客户 | 评分 | 问题类型 | 描述 |
    """
    path = os.path.join(config.DATA_DIR, "customer_feedback.md")
    if not os.path.exists(path):
        print("  跳过（文件不存在）：customer_feedback.md")
        return 0
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    rows = re.findall(
        r"\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*(\d)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
        content,
    )
    for customer, rating, issue_type, description in rows:
        session.add(
            FeedbackRecord(
                customer=customer.strip(),
                rating=int(rating),
                issue_type=issue_type.strip(),
                description=description.strip(),
                created_at=datetime.utcnow(),
            )
        )
    return len(rows)


def main():
    if not business_engine:
        print("业务数据库不可用，请检查 BUSINESS_DATABASE_URL 配置。")
        sys.exit(1)

    session = get_business_session()
    try:
        # 幂等：清空再灌
        session.query(SalesRecord).delete()
        session.query(FeedbackRecord).delete()

        n_sales = seed_sales(session)
        n_feedback = seed_feedback(session)
        session.commit()
        print(f"✅ 灌库完成：销售 {n_sales} 条，反馈 {n_feedback} 条")
        print(f"   目标库：{config.BUSINESS_DATABASE_URL}")

        # 演示账号（RBAC 三种角色）
        from app.repository.users import create_user

        create_user("admin", "admin123", "admin")
        create_user("sales_hd", "sales123", "sales", region="华东")
        create_user("viewer", "viewer123", "viewer")
        print("✅ 演示账号已创建：admin/admin123（管理员）、"
              "sales_hd/sales123（华东销售）、viewer/viewer123（只读）")
    except Exception as e:
        session.rollback()
        print(f"❌ 灌库失败：{e}")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
