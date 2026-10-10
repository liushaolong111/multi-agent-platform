"""Redis 短期记忆：会话上下文，TTL 自动过期。"""
import json
import logging

import redis

import config

logger = logging.getLogger(__name__)

redis_client = redis.Redis(
    host=config.REDIS_HOST,
    port=config.REDIS_PORT,
    db=config.REDIS_DB,
    decode_responses=True,
    socket_connect_timeout=2,
)


def store_session(session_id: str, data: dict, ttl: int = None):
    """存储会话数据，Redis 连不上时静默失败。"""
    if ttl is None:
        ttl = config.SESSION_TTL
    try:
        redis_client.setex(
            f"session:{session_id}", ttl, json.dumps(data, ensure_ascii=False)
        )
    except Exception as e:
        logger.warning("[Memory] Redis 写入失败（已跳过）：%s", e)


def get_session(session_id: str) -> dict:
    """获取会话数据，Redis 连不上时返回空字典。"""
    try:
        data = redis_client.get(f"session:{session_id}")
        return json.loads(data) if data else {}
    except Exception as e:
        logger.warning("[Memory] Redis 读取失败（已跳过）：%s", e)
        return {}


def update_session(session_id: str, key: str, value):
    """更新会话字段，Redis 连不上时静默失败。"""
    try:
        session = get_session(session_id)
        session[key] = value
        store_session(session_id, session)
    except Exception as e:
        logger.warning("[Memory] Redis 更新失败（已跳过）：%s", e)
