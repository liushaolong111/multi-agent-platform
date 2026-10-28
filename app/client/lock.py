"""Redis 分布式锁：SETNX + 唯一 token 校验 + 自动续期，防止并发冲突。

使用方式：
    with RedisLock("seed:business_db", ttl=30):
        # 临界区代码

实现要点：
- 用 SET key value NX EX seconds 原子获取锁
- value 是唯一 token（uuid），释放时校验 token，防止误删别人持有的锁
- 启动守护线程自动续期（看门狗），防止业务执行超时导致锁被别人抢走
- Redis 不可用时降级为进程内 threading.Lock（单进程内仍保证互斥）
"""
import logging
import threading
import time
import uuid

from app.client.short_term import _check_redis, redis_client

logger = logging.getLogger(__name__)


class RedisLock:
    """Redis 分布式锁，支持 with 语法。"""

    def __init__(self, name: str, ttl: int = 30, acquire_timeout: int = 10):
        """
        Args:
            name: 锁名称，作为 Redis key
            ttl: 锁过期时间（秒），续期线程会自动续期
            acquire_timeout: 获取锁的最大等待时间（秒）
        """
        self.name = f"lock:{name}"
        self.ttl = ttl
        self.acquire_timeout = acquire_timeout
        self._token = str(uuid.uuid4())
        self._renew_thread = None
        self._stop_renew = threading.Event()
        # 进程内锁，Redis 不可用时兜底
        self._local_lock = threading.Lock()

    def acquire(self) -> bool:
        """尝试获取锁，成功返回 True，超时返回 False。"""
        # Redis 不可用时用进程内锁
        if not _check_redis():
            logger.debug("[Lock] Redis 不可用，使用进程内锁：%s", self.name)
            return self._local_lock.acquire(timeout=self.acquire_timeout)

        start = time.time()
        while time.time() - start < self.acquire_timeout:
            # SET key value NX EX ttl：原子地设置锁
            ok = redis_client.set(self.name, self._token, nx=True, ex=self.ttl)
            if ok:
                logger.debug("[Lock] 获取锁成功：%s", self.name)
                self._start_renew()
                return True
            time.sleep(0.1)

        logger.warning("[Lock] 获取锁超时：%s", self.name)
        return False

    def release(self) -> None:
        """释放锁：校验 token 后删除，防止误删别人的锁。"""
        self._stop_renew.set()
        if self._renew_thread and self._renew_thread.is_alive():
            self._renew_thread.join(timeout=2)

        # Redis 不可用时释放进程内锁
        if not _check_redis():
            self._local_lock.release()
            return

        # 用 Lua 脚本原子地"校验 token + 删除"，避免 check-then-act 竞态
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            redis_client.eval(lua_script, 1, self.name, self._token)
            logger.debug("[Lock] 释放锁成功：%s", self.name)
        except Exception as e:
            logger.warning("[Lock] 释放锁失败：%s", e)

    def _start_renew(self) -> None:
        """启动看门狗线程，定期续期锁。"""
        def renew():
            while not self._stop_renew.is_set():
                time.sleep(self.ttl / 3)
                if self._stop_renew.is_set():
                    break
                try:
                    # 只续期自己持有的锁
                    lua = """
                    if redis.call("get", KEYS[1]) == ARGV[1] then
                        return redis.call("expire", KEYS[1], ARGV[2])
                    else
                        return 0
                    end
                    """
                    redis_client.eval(lua, 1, self.name, self._token, self.ttl)
                except Exception as e:
                    logger.warning("[Lock] 续期失败：%s", e)

        self._renew_thread = threading.Thread(target=renew, daemon=True)
        self._renew_thread.start()

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"获取分布式锁超时：{self.name}")
        return self

    def __exit__(self, *args):
        self.release()
