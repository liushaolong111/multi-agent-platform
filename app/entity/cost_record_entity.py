"""成本流水业务实体。"""
from datetime import datetime

from pydantic.dataclasses import dataclass


@dataclass
class CostRecordEntity:
    """成本流水实体，对应 cost_records 表。"""

    id: int | None = None
    project_code: str | None = None
    project_name: str | None = None
    region: str | None = None
    cost_month: str | None = None
    cost_category: str | None = None
    amount: float | None = None
    budget_amount: float | None = None
    created_at: datetime | None = None
