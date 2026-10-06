import redis
import json
import os

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6380))

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=0,
    decode_responses=True,
    socket_connect_timeout=2
)


def store_session(session_id: str, data: dict, ttl: int = 3600):
    """存储会话数据，Redis 连不上时静默失败"""
    try:
        redis_client.setex(f"session:{session_id}", ttl, json.dumps(data, ensure_ascii=False))
    except Exception as e:
        print(f"[Memory] Redis 写入失败（已跳过）：{e}")


def get_session(session_id: str) -> dict:
    """获取会话数据，Redis 连不上时返回空字典"""
    try:
        data = redis_client.get(f"session:{session_id}")
        return json.loads(data) if data else {}
    except Exception as e:
        print(f"[Memory] Redis 读取失败（已跳过）：{e}")
        return {}


def update_session(session_id: str, key: str, value):
    """更新会话字段，Redis 连不上时静默失败"""
    try:
        session = get_session(session_id)
        session[key] = value
        store_session(session_id, session)
    except Exception as e:
        print(f"[Memory] Redis 更新失败（已跳过）：{e}")