"""业务库实体：销售流水与客户反馈。

设计要点：
- 独立于对话记忆库（app/repository/long_term.py）
- 默认 SQLite（data/business.db），生产切 PostgreSQL 只需改环境变量
"""
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, Integer, String, Text

from app.model.base import Base


class SalesRecord(Base):
    """销售流水表：一行 = 一笔销售记录。"""

    __tablename__ = "sales_records"

    id = Column(Integer, primary_key=True)
    sale_date = Column(Date, nullable=False, index=True, comment="销售日期")
    product = Column(String(100), nullable=False, index=True, comment="产品名称")
    region = Column(String(50), nullable=False, index=True, comment="销售区域")
    amount = Column(Float, nullable=False, comment="销售额（元）")
    quantity = Column(Integer, nullable=False, comment="销量（件）")
    created_at = Column(DateTime, default=datetime.utcnow)


class FeedbackRecord(Base):
    """客户反馈表：一行 = 一条客户反馈。"""

    __tablename__ = "feedback_records"

    id = Column(Integer, primary_key=True)
    customer = Column(String(100), nullable=False, comment="客户名称")
    rating = Column(Integer, nullable=False, comment="评分（1-5）")
    issue_type = Column(String(100), nullable=False, index=True, comment="问题类型")
    description = Column(Text, comment="问题描述")
    created_at = Column(DateTime, default=datetime.utcnow)
