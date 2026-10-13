"""轻量级运行指标采集：token 用量、LLM 延迟、节点延迟、请求计数。

进程内内存聚合，通过 /metrics 端点暴露快照；进程重启即清零。
如需持久化和分布式聚合，可平滑替换为 Prometheus client。
"""
import threading
import time
from collections import defaultdict


class Metrics:
    """线程安全的指标聚合器。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.request_count = 0
        self.request_errors = 0
        self.llm_calls = 0
        self.llm_errors = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.tool_calls = defaultdict(int)  # 工具名 -> 调用次数
        self._llm_latencies = []  # 毫秒，只保留最近 500 条
        self._node_latencies = defaultdict(list)  # 节点名 -> 延迟列表

    # ---- 写入 ----

    def record_request(self, error: bool = False):
        with self._lock:
            self.request_count += 1
            if error:
                self.request_errors += 1

    def record_llm_call(
        self,
        latency_ms: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        error: bool = False,
    ):
        with self._lock:
            self.llm_calls += 1
            if error:
                self.llm_errors += 1
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens
            self._llm_latencies.append(latency_ms)
            if len(self._llm_latencies) > 500:
                self._llm_latencies = self._llm_latencies[-500:]

    def record_node(self, name: str, latency_ms: float):
        with self._lock:
            lst = self._node_latencies[name]
            lst.append(latency_ms)
            if len(lst) > 500:
                self._node_latencies[name] = lst[-500:]

    def record_tool(self, name: str):
        with self._lock:
            self.tool_calls[name] += 1

    # ---- 读取 ----

    @staticmethod
    def _p95(values: list) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = min(int(len(ordered) * 0.95), len(ordered) - 1)
        return round(ordered[idx], 1)

    def snapshot(self) -> dict:
        with self._lock:
            node_stats = {
                name: {
                    "count": len(vals),
                    "avg_ms": round(sum(vals) / len(vals), 1) if vals else 0,
                    "p95_ms": self._p95(vals),
                }
                for name, vals in self._node_latencies.items()
            }
            return {
                "uptime_seconds": int(time.time() - self.started_at),
                "requests": {
                    "total": self.request_count,
                    "errors": self.request_errors,
                },
                "llm": {
                    "calls": self.llm_calls,
                    "errors": self.llm_errors,
                    "prompt_tokens": self.prompt_tokens,
                    "completion_tokens": self.completion_tokens,
                    "total_tokens": self.prompt_tokens + self.completion_tokens,
                    "avg_latency_ms": round(
                        sum(self._llm_latencies) / len(self._llm_latencies), 1
                    )
                    if self._llm_latencies
                    else 0,
                    "p95_latency_ms": self._p95(self._llm_latencies),
                },
                "tool_calls": dict(self.tool_calls),
                "nodes": node_stats,
            }


# 全局单例
metrics = Metrics()
