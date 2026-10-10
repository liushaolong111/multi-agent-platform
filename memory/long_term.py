"""PostgreSQL 长期记忆：跨会话持久化存储用户交互历史。"""
import logging
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

import config

logger = logging.getLogger(__name__)

if not config.DATABASE_URL:
    logger.warning(
        "[Memory] DATABASE_URL 未配置，长期记忆功能将不可用。"
        "请在 .env 中设置 DATABASE_URL。"
    )

engine = create_engine(config.DATABASE_URL) if config.DATABASE_URL else None
Session = sessionmaker(bind=engine) if engine else None
Base = declarative_base()


class ConversationMemory(Base):
    __tablename__ = "conversation_memory"
    id = Column(Integer, primary_key=True)
    user_id = Column(String(100), index=True)
    session_id = Column(String(100), index=True)
    content = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


if engine:
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        logger.warning("[Memory] 长期记忆表创建失败（数据库不可达？）：%s", e)
        Session = None


def store_long_term(user_id: str, session_id: str, content: dict):
    """存储长期记忆；数据库未配置时静默跳过。"""
    if not Session:
        logger.warning("[Memory] 长期记忆未配置，跳过存储。")
        return
    try:
        with Session() as db:
            mem = ConversationMemory(
                user_id=user_id, session_id=session_id, content=content
            )
            db.add(mem)
            db.commit()
    except Exception as e:
        logger.warning("[Memory] 长期记忆写入失败：%s", e)


def get_long_term(user_id: str, limit: int = 10):
    """获取用户的长期记忆；数据库未配置时返回空列表。"""
    if not Session:
        return []
    try:
        with Session() as db:
            return (
                db.query(ConversationMemory)
                .filter_by(user_id=user_id)
                .order_by(ConversationMemory.created_at.desc())
                .limit(limit)
                .all()
            )
    except Exception as e:
        logger.warning("[Memory] 长期记忆读取失败：%s", e)
        return []
