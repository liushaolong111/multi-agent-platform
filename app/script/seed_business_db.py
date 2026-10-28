"""业务库种子脚本：把 data/ 下的工程项目 CSV 灌入业务数据库。

用法：
    .venv/Scripts/python.exe app/script/seed_business_db.py

幂等：每次运行先清空表再插入，可重复执行。
真实业务场景中这一步对应"从项目管理系统 / 成本系统定时同步"，
此处用项目自带 demo 数据模拟。

历史遗留：早期版本的 sales_records / feedback_records 属于销售域，已随业务原型
切换为工程项目域而废弃，脚本会主动 DROP 掉，避免旧表残留在库里造成混淆。
"""
import os
import sys

import pandas as pd
from sqlalchemy import text

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.client.lock import RedisLock
from app.conf import config
from app.entity.cost_record_entity import CostRecordEntity
from app.entity.material_record_entity import MaterialRecordEntity
from app.entity.project_entity import ProjectEntity
from app.entity.safety_issue_entity import SafetyIssueEntity
from app.mapper.cost_record_mapper import CostRecordMapper
from app.mapper.material_record_mapper import MaterialRecordMapper
from app.mapper.project_mapper import ProjectMapper
from app.mapper.safety_issue_mapper import SafetyIssueMapper
from app.model.base import Base
from app.model.business import CostRecord, MaterialRecord, Project, SafetyIssue
from app.repository.business import business_engine, get_business_session

LEGACY_TABLES = ("sales_records", "feedback_records")


def drop_legacy_tables() -> None:
    """删除销售域遗留表（幂等，表不存在也不报错）。"""
    with business_engine.begin() as conn:
        for table in LEGACY_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))


def _read_csv(filename: str) -> pd.DataFrame | None:
    path = os.path.join(config.DATA_DIR, filename)
    if not os.path.exists(path):
        print(f"  跳过（文件不存在）：{filename}")
        return None
    return pd.read_csv(path)


def seed_projects(session) -> int:
    """从 projects.csv 灌入工程项目（entity → mapper → model 分层）。"""
    df = _read_csv("projects.csv")
    if df is None:
        return 0
    for _, row in df.iterrows():
        entity = ProjectEntity(
            project_code=str(row["项目编号"]),
            project_name=str(row["项目名称"]),
            region=str(row["区域"]),
            manager_name=str(row["项目经理"]),
            status=str(row["项目状态"]),
            contract_amount=float(row["合同金额"]),
            start_date=pd.to_datetime(row["开工日期"]).date(),
            planned_end_date=pd.to_datetime(row["计划竣工日期"]).date(),
        )
        session.add(ProjectMapper.to_model(entity))
    return len(df)


def seed_costs(session) -> int:
    """从 cost_records.csv 灌入项目成本流水（entity → mapper → model 分层）。"""
    df = _read_csv("cost_records.csv")
    if df is None:
        return 0
    for _, row in df.iterrows():
        entity = CostRecordEntity(
            project_code=str(row["项目编号"]),
            project_name=str(row["项目名称"]),
            region=str(row["区域"]),
            cost_month=str(row["成本月份"]),
            cost_category=str(row["成本类别"]),
            amount=float(row["金额"]),
            budget_amount=float(row["预算金额"]),
        )
        session.add(CostRecordMapper.to_model(entity))
    return len(df)


def seed_safety_issues(session) -> int:
    """从 safety_issues.csv 灌入质量安全问题（entity → mapper → model 分层）。"""
    df = _read_csv("safety_issues.csv")
    if df is None:
        return 0
    for _, row in df.iterrows():
        closed = row.get("整改完成日期")
        entity = SafetyIssueEntity(
            project_code=str(row["项目编号"]),
            project_name=str(row["项目名称"]),
            region=str(row["区域"]),
            issue_type=str(row["问题类型"]),
            severity=str(row["严重程度"]),
            status=str(row["状态"]),
            found_date=pd.to_datetime(row["发现日期"]).date(),
            closed_date=None if pd.isna(closed) or closed == "" else pd.to_datetime(closed).date(),
            description=str(row["问题描述"]),
        )
        session.add(SafetyIssueMapper.to_model(entity))
    return len(df)


def seed_materials(session) -> int:
    """从 material_records.csv 灌入材料库存（entity → mapper → model 分层）。"""
    df = _read_csv("material_records.csv")
    if df is None:
        return 0
    for _, row in df.iterrows():
        entity = MaterialRecordEntity(
            project_code=str(row["项目编号"]),
            project_name=str(row["项目名称"]),
            region=str(row["区域"]),
            material_category=str(row["材料类别"]),
            material_name=str(row["材料名称"]),
            specification=str(row.get("规格型号", "")),
            unit=str(row["单位"]),
            stock_qty=int(row["库存数量"]),
            eta_date=pd.to_datetime(row["预计到货日期"]).date(),
            plan_use_date=pd.to_datetime(row["计划使用日期"]).date(),
            supplier=str(row.get("供应商", "")),
        )
        session.add(MaterialRecordMapper.to_model(entity))
    return len(df)


def main():
    if not business_engine:
        print("业务数据库不可用，请检查 BUSINESS_DATABASE_URL 配置。")
        sys.exit(1)

    Base.metadata.create_all(business_engine)
    drop_legacy_tables()

    session = get_business_session()
    # 分布式锁：防止并发灌库导致数据重复或冲突
    with RedisLock("seed:business_db", ttl=120, acquire_timeout=30):
        try:
            # 幂等：清空再灌
            session.query(MaterialRecord).delete()
            session.query(SafetyIssue).delete()
            session.query(CostRecord).delete()
            session.query(Project).delete()

            n_projects = seed_projects(session)
            n_costs = seed_costs(session)
            n_issues = seed_safety_issues(session)
            n_materials = seed_materials(session)
            session.commit()
            print(
                f"✅ 灌库完成：项目 {n_projects} 个，成本流水 {n_costs} 条，"
                f"质量问题 {n_issues} 条，材料库存 {n_materials} 条"
            )
            print(f"   目标库：{config.BUSINESS_DATABASE_URL}")

            # 演示账号（RBAC 三种角色，按区域公司隔离）
            from app.repository.users import create_user

            create_user("admin", "admin123", "admin")
            create_user("manager_hd", "manager123", "manager", region="华东")
            create_user("viewer", "viewer123", "viewer")
            print("✅ 演示账号已创建：admin/admin123（集团管理员）、"
                  "manager_hd/manager123（华东区域经理）、viewer/viewer123（只读）")
        except Exception as e:
            session.rollback()
            print(f"❌ 灌库失败：{e}")
            sys.exit(1)
        finally:
            session.close()


if __name__ == "__main__":
    main()
