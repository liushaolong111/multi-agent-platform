"""工程项目业务实体。"""
from datetime import date, datetime

from pydantic.dataclasses import dataclass


@dataclass
class ProjectEntity:
    """工程项目实体，对应 projects 表。"""

    id: int | None = None
    project_code: str | None = None
    project_name: str | None = None
    region: str | None = None
    manager_name: str | None = None
    status: str | None = None
    contract_amount: float | None = None
    start_date: date | None = None
    planned_end_date: date | None = None
    created_at: datetime | None = None
