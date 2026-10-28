"""业务库 ORM 模型：工程项目、成本流水、质量安全问题与材料库存表映射。

设计要点：
- 独立于对话记忆库（app/repository/long_term.py）
- 默认 SQLite（data/business.db），生产切 MySQL / PostgreSQL 只需改环境变量
- 区域（region）字段是 RBAC 行级隔离的唯一依据，所有表都带该字段
"""
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, Integer, String, Text

from app.model.base import Base


class Project(Base):
    """工程项目表：一行 = 一个在建/竣工/停工项目。"""

    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    project_code = Column(String(30), unique=True, nullable=False, index=True, comment="项目编号")
    project_name = Column(String(120), nullable=False, index=True, comment="项目名称")
    region = Column(String(50), nullable=False, index=True, comment="区域公司")
    manager_name = Column(String(50), nullable=False, comment="项目经理")
    status = Column(String(20), nullable=False, index=True, comment="项目状态：在建/竣工/停工")
    contract_amount = Column(Float, nullable=False, comment="合同金额（元）")
    start_date = Column(Date, nullable=False, comment="开工日期")
    planned_end_date = Column(Date, nullable=False, comment="计划竣工日期")
    created_at = Column(DateTime, default=datetime.utcnow)


class CostRecord(Base):
    """成本流水表：一行 = 某项目某月某成本类别的实际成本与预算。"""

    __tablename__ = "cost_records"

    id = Column(Integer, primary_key=True)
    project_code = Column(String(30), nullable=False, index=True, comment="项目编号")
    project_name = Column(String(120), nullable=False, comment="项目名称")
    region = Column(String(50), nullable=False, index=True, comment="区域公司")
    cost_month = Column(String(7), nullable=False, index=True, comment="成本月份，如 2026-05")
    cost_category = Column(
        String(20), nullable=False, index=True, comment="成本类别：材料/人工/机械/分包"
    )
    amount = Column(Float, nullable=False, comment="实际成本（元）")
    budget_amount = Column(Float, nullable=False, comment="预算金额（元）")
    created_at = Column(DateTime, default=datetime.utcnow)


class SafetyIssue(Base):
    """质量安全问题表：一行 = 一条安全或质量隐患及其整改状态。"""

    __tablename__ = "safety_issues"

    id = Column(Integer, primary_key=True)
    project_code = Column(String(30), nullable=False, index=True, comment="项目编号")
    project_name = Column(String(120), nullable=False, comment="项目名称")
    region = Column(String(50), nullable=False, index=True, comment="区域公司")
    issue_type = Column(String(20), nullable=False, index=True, comment="问题类型：安全/质量")
    severity = Column(String(20), nullable=False, index=True, comment="严重程度：一般/较大/重大")
    status = Column(String(20), nullable=False, index=True, comment="状态：待整改/整改中/已闭环")
    found_date = Column(Date, nullable=False, index=True, comment="发现日期")
    closed_date = Column(Date, nullable=True, comment="整改完成日期（未闭环为空）")
    description = Column(Text, comment="问题描述")
    created_at = Column(DateTime, default=datetime.utcnow)


class MaterialRecord(Base):
    """材料库存表：一行 = 某项目某材料的库存与到货计划。"""

    __tablename__ = "material_records"

    id = Column(Integer, primary_key=True)
    project_code = Column(String(30), nullable=False, index=True, comment="项目编号")
    project_name = Column(String(120), nullable=False, comment="项目名称")
    region = Column(String(50), nullable=False, index=True, comment="区域公司")
    material_category = Column(String(20), nullable=False, index=True, comment="材料类别")
    material_name = Column(String(80), nullable=False, comment="材料名称")
    specification = Column(String(50), comment="规格型号")
    unit = Column(String(10), nullable=False, comment="单位")
    stock_qty = Column(Integer, nullable=False, comment="库存数量")
    eta_date = Column(Date, nullable=False, index=True, comment="预计到货日期")
    plan_use_date = Column(Date, nullable=False, index=True, comment="计划使用日期")
    supplier = Column(String(80), comment="供应商")
    created_at = Column(DateTime, default=datetime.utcnow)
