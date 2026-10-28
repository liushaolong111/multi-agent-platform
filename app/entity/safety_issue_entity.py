"""质量安全问题业务实体。"""
from datetime import date, datetime

from pydantic.dataclasses import dataclass


@dataclass
class SafetyIssueEntity:
    """质量安全问题实体，对应 safety_issues 表。"""

    id: int | None = None
    project_code: str | None = None
    project_name: str | None = None
    region: str | None = None
    issue_type: str | None = None
    severity: str | None = None
    status: str | None = None
    found_date: date | None = None
    closed_date: date | None = None
    description: str | None = None
    created_at: datetime | None = None
