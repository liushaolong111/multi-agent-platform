"""MaterialRecord 模型与实体转换器。"""
from dataclasses import asdict

from app.entity.material_record_entity import MaterialRecordEntity
from app.model.business import MaterialRecord


class MaterialRecordMapper:
    @staticmethod
    def to_entity(model: MaterialRecord) -> MaterialRecordEntity:
        return MaterialRecordEntity(
            id=model.id,
            project_code=model.project_code,
            project_name=model.project_name,
            region=model.region,
            material_category=model.material_category,
            material_name=model.material_name,
            specification=model.specification,
            unit=model.unit,
            stock_qty=model.stock_qty,
            eta_date=model.eta_date,
            plan_use_date=model.plan_use_date,
            supplier=model.supplier,
            created_at=model.created_at,
        )

    @staticmethod
    def to_model(entity: MaterialRecordEntity) -> MaterialRecord:
        return MaterialRecord(**asdict(entity))
