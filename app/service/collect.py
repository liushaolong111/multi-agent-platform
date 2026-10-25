"""文档收集模块：从外部数据源拉取数据，转换后落盘到 data/ 目录。

设计要点：
- DataCollector 基类统一接口，新数据源只需继承并实现 fetch/convert
- create_collector() 简单工厂，对应面试里讲的"简单工厂 + 面向对象扩展"
- 增量检测：对比上次收集时间与目标文件 mtime，未变化则跳过
- 失败降级：单个数据源拉取失败打印警告继续，不阻塞其他数据源
- 收集完成后可选择触发 ingest.build_index() 重建向量索引
"""

from __future__ import annotations

import abc
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

# 项目根目录：app/service/collect.py 往上三级
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 允许以 `python app/service/collect.py` 方式直接运行时，from app.xxx import 能工作
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DATA_DIR = PROJECT_ROOT / "data"
STATE_FILE = PROJECT_ROOT / "data" / ".collect_state.json"

# 收集间隔（秒）：默认 6 小时内不重复拉同一数据源
DEFAULT_INTERVAL_SECONDS = 6 * 3600


# ---------------------------------------------------------------------------
# 状态管理：记录每个数据源上次成功收集的时间戳
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class CollectedItem:
    """一份收集到的产物，包含内容和要写入的文件名。"""
    filename: str
    content: str
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 抽象基类：统一收集接口
# ---------------------------------------------------------------------------

class DataCollector(abc.ABC):
    """所有数据源的抽象基类。

    子类只需实现 fetch() 和 convert() 两个方法。
    """

    source_type: str = "base"  # 工厂分发用的标识

    def __init__(self, data_dir: str | os.PathLike = DATA_DIR):
        self.data_dir = Path(data_dir)

    @abc.abstractmethod
    def fetch(self) -> dict | list[dict] | None:
        """从外部拉取原始数据。网络异常时返回 None。"""

    @abc.abstractmethod
    def convert(self, raw: dict | list[dict]) -> list[CollectedItem]:
        """把原始数据转成要落盘的文件列表。"""

    def collect(self, force: bool = False) -> list[str]:
        """完整的 拉取 → 转换 → 落盘 流程。

        Args:
            force: 是否跳过增量检测，强制重新拉取。

        Returns:
            成功写入的文件路径列表。
        """
        state = _load_state()
        last_ts = state.get(self.source_type, 0)
        now = time.time()
        if not force and (now - last_ts) < DEFAULT_INTERVAL_SECONDS:
            remaining = int(DEFAULT_INTERVAL_SECONDS - (now - last_ts))
            print(f"[{self.source_type}] 距上次收集未满 {DEFAULT_INTERVAL_SECONDS // 3600}h "
                  f"（还剩 {remaining // 60}min），跳过。force=True 可强制拉取。")
            return []

        print(f"[{self.source_type}] 开始拉取……")
        raw = self.fetch()
        if raw is None:
            print(f"[{self.source_type}] 拉取失败，跳过。")
            return []

        items = self.convert(raw)
        written: list[str] = []
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for item in items:
            path = self.data_dir / item.filename
            path.write_text(item.content, encoding="utf-8")
            written.append(str(path))
            print(f"[{self.source_type}] 写入：{item.filename}")

        state[self.source_type] = now
        _save_state(state)
        return written


# ---------------------------------------------------------------------------
# 具体实现：Open-Meteo 公开天气 API
# ---------------------------------------------------------------------------

class OpenMeteoCollector(DataCollector):
    """收集北京未来 7 天天气预测，产出 CSV + Markdown 两份。"""

    source_type = "open_meteo"
    API_URL = "https://api.open-meteo.com/v1/forecast"
    DEFAULT_PARAMS = {
        "latitude": 39.9042,         # 北京
        "longitude": 116.4074,
        "current": "temperature_2m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
        "timezone": "Asia/Shanghai",
        "forecast_days": 7,
    }

    def __init__(self, *, city_name: str = "北京", params: dict | None = None,
                 data_dir: str | os.PathLike = DATA_DIR,
                 timeout: float = 10.0):
        super().__init__(data_dir)
        self.city_name = city_name
        self.params = {**self.DEFAULT_PARAMS, **(params or {})}
        self.timeout = timeout

    def fetch(self) -> dict | None:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(self.API_URL, params=self.params)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("Open-Meteo 返回 %s: %s", exc.response.status_code, exc)
            return None
        except httpx.RequestError as exc:
            logger.warning("Open-Meteo 请求失败：%s", exc)
            return None

    def convert(self, raw: dict) -> list[CollectedItem]:
        daily = raw.get("daily", {})
        dates = daily.get("time", [])
        t_max = daily.get("temperature_2m_max", [])
        t_min = daily.get("temperature_2m_min", [])
        precipitation = daily.get("precipitation_sum", [])

        # --- CSV ---
        header = "date,temp_max_c,temp_min_c,precipitation_mm"
        rows = []
        for i in range(len(dates)):
            rows.append(f"{dates[i]},{t_max[i]},{t_min[i]},{precipitation[i]}")
        csv_content = "\n".join([header, *rows]) + "\n"

        # --- Markdown（给 RAG 检索用的自然语言摘要）---
        md_lines = [
            f"# {self.city_name} 未来 7 天天气预报",
            "",
            f"数据来源：Open-Meteo 公开 API（{raw.get('timezone', 'Asia/Shanghai')}）",
            "",
            "| 日期 | 最高温(°C) | 最低温(°C) | 降水(mm) |",
            "|---|---|---|---|",
        ]
        for i in range(len(dates)):
            md_lines.append(
                f"| {dates[i]} | {t_max[i]} | {t_min[i]} | {precipitation[i]} |"
            )
        md_lines.append("")
        md_lines.append(
            f"当前温度：{raw.get('current', {}).get('temperature_2m', 'N/A')} °C "
            f"（{raw.get('current', {}).get('time', 'N/A')}）"
        )
        md_content = "\n".join(md_lines) + "\n"

        return [
            CollectedItem(filename="weather_beijing.csv", content=csv_content),
            CollectedItem(filename="weather_beijing.md", content=md_content),
        ]


# ---------------------------------------------------------------------------
# 简单工厂 + 统一入口
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[DataCollector]] = {
    OpenMeteoCollector.source_type: OpenMeteoCollector,
}


def create_collector(source_type: str, **kwargs) -> DataCollector:
    """简单工厂：按 source_type 创建对应 Collector 实例。

    新增数据源 = 继承 DataCollector + 注册到 _REGISTRY，工厂本身不改。
    """
    if source_type not in _REGISTRY:
        raise KeyError(
            f"未知的数据源类型：{source_type!r}，"
            f"可选：{sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[source_type](**kwargs)


def collect_all(
    sources: Iterable[str] | None = None,
    force: bool = False,
    rebuild_index: bool = True,
) -> dict[str, list[str]]:
    """一键收集所有（或指定）数据源，并可选重建向量索引。

    Args:
        sources: 要收集的 source_type 列表，默认全部。
        force: 是否跳过增量检测强制拉取。
        rebuild_index: 收集完成后是否调用 ingest.build_index()。

    Returns:
        {source_type: [写入的文件路径]} 映射。
    """
    if sources is None:
        sources = list(_REGISTRY.keys())

    results: dict[str, list[str]] = {}
    for st in sources:
        try:
            collector = create_collector(st)
            written = collector.collect(force=force)
            results[st] = written
        except Exception as exc:
            logger.exception("[%s] 收集异常：%s", st, exc)
            results[st] = []

    any_written = any(v for v in results.values())
    if rebuild_index and any_written:
        print("\n=== 触发重建向量索引 ===")
        # 延迟 import，避免循环依赖
        from app.script.ingest import build_index
        build_index(str(DATA_DIR), "faiss_index")
    elif not any_written:
        print("\n没有新数据落盘，跳过索引重建。")

    return results


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="从外部数据源收集知识文档")
    parser.add_argument(
        "--source", "-s",
        action="append",
        default=None,
        help="指定要收集的 source_type（可多次指定），默认全部。可选："
             + ", ".join(sorted(_REGISTRY.keys())),
    )
    parser.add_argument("--force", "-f", action="store_true",
                        help="跳过增量检测，强制重新拉取。")
    parser.add_argument("--no-rebuild", action="store_true",
                        help="收集完成后不触发 FAISS 索引重建。")
    args = parser.parse_args()

    collect_all(
        sources=args.source,
        force=args.force,
        rebuild_index=not args.no_rebuild,
    )
