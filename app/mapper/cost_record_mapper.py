"""CostRecord 模型与实体转换器。"""
from dataclasses import asdict

from app.entity.cost_record_entity import CostRecordEntity
from app.model.business import CostRecord


class CostRecordMapper:
    @staticmethod
    def to_entity(model: CostRecord) -> CostRecordEntity:
        return CostRecordEntity(
            id=model.id,
            project_code=model.project_code,
            project_name=model.project_name,
            region=model.region,
            cost_month=model.cost_month,
            cost_category=model.cost_category,
            amount=model.amount,
            budget_amount=model.budget_amount,
            created_at=model.created_at,
        )

    @staticmethod
    def to_model(entity: CostRecordEntity) -> CostRecord:
        return CostRecord(**asdict(entity))
