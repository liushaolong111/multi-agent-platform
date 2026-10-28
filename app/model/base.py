"""ORM 基类：用户与业务表的模型共用一个 declarative Base。"""
from sqlalchemy.orm import declarative_base

Base = declarative_base()
