"""User 模型与实体转换器。"""
from dataclasses import asdict

from app.entity.user_entity import UserEntity
from app.model.user import User


class UserMapper:
    @staticmethod
    def to_entity(model: User) -> UserEntity:
        return UserEntity(
            id=model.id,
            username=model.username,
            password_hash=model.password_hash,
            role=model.role,
            region=model.region,
        )

    @staticmethod
    def to_model(entity: UserEntity) -> User:
        return User(**asdict(entity))
