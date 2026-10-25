"""用户实体：用户表结构与角色常量。

设计要点：
- 用户存业务库（与业务数据同库，一套连接管理）
- 角色 → 数据权限（data scope）：
    admin  ：全部数据
    sales  ：仅本区域销售数据（行级隔离，SQL 层强制）
    viewer ：全部数据但只读（当前所有查询本就只读，预留角色）
"""
from sqlalchemy import Column, Integer, String

from app.model.base import Base

# 角色常量
ROLE_ADMIN = "admin"
ROLE_SALES = "sales"
ROLE_VIEWER = "viewer"
VALID_ROLES = {ROLE_ADMIN, ROLE_SALES, ROLE_VIEWER}


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    role = Column(String(20), nullable=False, default=ROLE_VIEWER)
    region = Column(String(50), default="", comment="sales 角色负责的区域；admin/viewer 为空")
