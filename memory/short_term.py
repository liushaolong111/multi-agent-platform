import redis
import json
import os

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6380))

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=0,
    decode_responses=True
)


def store_session(session_id: str, data: dict, ttl: int = 3600):
    """存储会话数据，默认1小时后过期"""
    redis_client.setex(f"session:{session_id}", ttl, json.dumps(data, ensure_ascii=False))


def get_session(session_id: str) -> dict:
    """获取会话数据"""
    data = redis_client.get(f"session:{session_id}")
    return json.loads(data) if data else {}


def update_session(session_id: str, key: str, value):
    """更新会话中的某个字段"""
    session = get_session(session_id)
    session[key] = value
    store_session(session_id, session)