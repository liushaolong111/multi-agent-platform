"""短期记忆：优先用 Redis，不可达时自动降级为进程内内存存储。

这样即使没有 Redis，项目也能开箱即用（面试演示友好）。
"""
import json
import logging
import time
from typing import Dict, Tuple

import redis

import config

logger = logging.getLogger(__name__)


class _MemoryStore:
    """进程内内存存储，带 TTL，作为 Redis 不可用时的兜底。"""

    def __init__(self):
        self._data: Dict[str, Tuple[str, float]] = {}  # key -> (json_str, expire_at)

    def setex(self, key: str, ttl: int, value: str):
        self._data[key] = (value, time.time() + ttl)

    def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        value, expire_at = item
        if time.time() > expire_at:
            del self._data[key]
            return None
        return value


# 尝试连接 Redis；失败则用内存存储
redis_client = redis.Redis(
    host=config.REDIS_HOST,
    port=config.REDIS_PORT,
    db=config.REDIS_DB,
    decode_responses=True,
    socket_connect_timeout=2,
)
_memory_store = _MemoryStore()
_use_redis = True


def _check_redis() -> bool:
    """检测 Redis 是否可用（只在第一次调用时检测）。"""
    global _use_redis
    if not _use_redis:
        return False
    try:
        redis_client.ping()
        return True
    except Exception:
        logger.warning(
            "[Memory] Redis 不可达（%s:%s），降级为进程内内存存储",
            config.REDIS_HOST,
            config.REDIS_PORT,
        )
        _use_redis = False
        return False


def store_session(session_id: str, data: dict, ttl: int = None):
    """存储会话数据。"""
    if ttl is None:
        ttl = config.SESSION_TTL
    key = f"session:{session_id}"
    value = json.dumps(data, ensure_ascii=False)
    try:
        if _check_redis():
            redis_client.setex(key, ttl, value)
        else:
            _memory_store.setex(key, ttl, value)
    except Exception as e:
        logger.warning("[Memory] 短期记忆写入失败：%s", e)


def get_session(session_id: str) -> dict:
    """获取会话数据。"""
    key = f"session:{session_id}"
    try:
        if _check_redis():
            data = redis_client.get(key)
        else:
            data = _memory_store.get(key)
        return json.loads(data) if data else {}
    except Exception as e:
        logger.warning("[Memory] 短期记忆读取失败：%s", e)
        return {}


def update_session(session_id: str, key: str, value):
    """更新会话字段。"""
    try:
        session = get_session(session_id)
        session[key] = value
        store_session(session_id, session)
    except Exception as e:
        logger.warning("[Memory] 短期记忆更新失败：%s", e)
