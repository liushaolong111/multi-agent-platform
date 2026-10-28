"""用户业务实体。"""
from pydantic.dataclasses import dataclass


@dataclass
class UserEntity:
    """用户实体，对应 users 表。"""

    id: int | None = None
    username: str | None = None
    password_hash: str | None = None
    role: str | None = None
    region: str | None = None
