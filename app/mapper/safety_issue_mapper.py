"""SafetyIssue 模型与实体转换器。"""
from dataclasses import asdict

from app.entity.safety_issue_entity import SafetyIssueEntity
from app.model.business import SafetyIssue


class SafetyIssueMapper:
    @staticmethod
    def to_entity(model: SafetyIssue) -> SafetyIssueEntity:
        return SafetyIssueEntity(
            id=model.id,
            project_code=model.project_code,
            project_name=model.project_name,
            region=model.region,
            issue_type=model.issue_type,
            severity=model.severity,
            status=model.status,
            found_date=model.found_date,
            closed_date=model.closed_date,
            description=model.description,
            created_at=model.created_at,
        )

    @staticmethod
    def to_model(entity: SafetyIssueEntity) -> SafetyIssue:
        return SafetyIssue(**asdict(entity))
