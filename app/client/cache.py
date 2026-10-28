"""Redis 缓存封装：带 TTL 的键值缓存，Redis 不可达时降级为进程内内存。

适用于：
- pandas 预计算统计结果（compute_stats 等），避免每次请求都重新读 CSV
- schema 描述等基本不变的数据

与 short_term.py 的区别：
- short_term 存的是会话上下文（按 session_id）
- cache 存的是计算结果（按业务 key + region）
"""
import json
import logging
from typing import Any

from app.client.short_term import _check_redis, _memory_store, redis_client

logger = logging.getLogger(__name__)

# 默认缓存 TTL（秒）
DEFAULT_TTL = 300


def set_cache(key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:
    """写入缓存（Redis 优先，内存兜底）。"""
    serialized = json.dumps(value, ensure_ascii=False)
    try:
        if _check_redis():
            redis_client.setex(key, ttl, serialized)
        else:
            _memory_store.setex(key, ttl, serialized)
    except Exception as e:
        logger.warning("[Cache] 写入失败：%s", e)


def get_cache(key: str) -> Any | None:
    """读取缓存（Redis 优先，内存兜底）。"""
    try:
        if _check_redis():
            data = redis_client.get(key)
        else:
            data = _memory_store.get(key)
        return json.loads(data) if data else None
    except Exception as e:
        logger.warning("[Cache] 读取失败：%s", e)
        return None


def delete_cache(key: str) -> None:
    """删除缓存。"""
    try:
        if _check_redis():
            redis_client.delete(key)
        else:
            _memory_store._data.pop(key, None)
    except Exception as e:
        logger.warning("[Cache] 删除失败：%s", e)


def cached(key_prefix: str, ttl: int = DEFAULT_TTL):
    """缓存装饰器：用函数参数拼 key，结果缓存 ttl 秒。

    用法：
        @cached("stats:cost", ttl=300)
        def compute_stats(region=None):
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # 用参数拼 key（简单实现，参数需可序列化）
            params = f"{args}:{sorted(kwargs.items())}"
            cache_key = f"{key_prefix}:{hash(params)}"
            cached_val = get_cache(cache_key)
            if cached_val is not None:
                return cached_val
            result = func(*args, **kwargs)
            set_cache(cache_key, result, ttl)
            return result
        wrapper.__wrapped__ = func  # 保留原函数引用，方便测试绕过缓存
        return wrapper
    return decorator
