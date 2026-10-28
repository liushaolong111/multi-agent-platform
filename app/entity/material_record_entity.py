"""材料库存业务实体。"""
from datetime import date, datetime

from pydantic.dataclasses import dataclass


@dataclass
class MaterialRecordEntity:
    """材料库存实体，对应 material_records 表。"""

    id: int | None = None
    project_code: str | None = None
    project_name: str | None = None
    region: str | None = None
    material_category: str | None = None
    material_name: str | None = None
    specification: str | None = None
    unit: str | None = None
    stock_qty: int | None = None
    eta_date: date | None = None
    plan_use_date: date | None = None
    supplier: str | None = None
    created_at: datetime | None = None
