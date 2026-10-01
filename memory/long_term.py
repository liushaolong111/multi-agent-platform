import os
from sqlalchemy import create_engine, Column, String, Integer, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()


class ConversationMemory(Base):
    __tablename__ = "conversation_memory"
    id = Column(Integer, primary_key=True)
    user_id = Column(String(100), index=True)
    session_id = Column(String(100), index=True)
    content = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


def store_long_term(user_id: str, session_id: str, content: dict):
    """存储长期记忆"""
    with Session() as db:
        mem = ConversationMemory(
            user_id=user_id,
            session_id=session_id,
            content=content
        )
        db.add(mem)
        db.commit()


def get_long_term(user_id: str, limit: int = 10):
    """获取用户的长期记忆"""
    with Session() as db:
        return db.query(ConversationMemory)\
            .filter_by(user_id=user_id)\
            .order_by(ConversationMemory.created_at.desc())\
            .limit(limit).all()