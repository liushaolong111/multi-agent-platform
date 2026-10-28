"""Project 模型与实体转换器。"""
from dataclasses import asdict

from app.entity.project_entity import ProjectEntity
from app.model.business import Project


class ProjectMapper:
    @staticmethod
    def to_entity(model: Project) -> ProjectEntity:
        return ProjectEntity(
            id=model.id,
            project_code=model.project_code,
            project_name=model.project_name,
            region=model.region,
            manager_name=model.manager_name,
            status=model.status,
            contract_amount=model.contract_amount,
            start_date=model.start_date,
            planned_end_date=model.planned_end_date,
            created_at=model.created_at,
        )

    @staticmethod
    def to_model(entity: ProjectEntity) -> Project:
        return Project(**asdict(entity))
