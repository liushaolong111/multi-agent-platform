"""一键演示：RBAC 数据权限在各类「数据出口」上的实际效果。

演示三层防线（同一份权限模型，贯穿所有出口）：
  第 1 层  prompt 约束   —— 注入 LLM 的数据权限硬约束（让模型主动生成带过滤的 SQL）
  第 2 层  SQL 静态校验  —— 执行前拦截越权 SQL（sql_within_scope）
  第 3 层  出口过滤      —— 工具 / 预计算统计 / 知识检索 / 图表 共用 scoped_region

运行：.venv/Scripts/python.exe app/script/demo_tool_permissions.py
无需 API Key、无需起服务（不经过 LLM，结果确定可复现）。
"""
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.agent.nodes.text2sql import set_current_user  # noqa: E402
from app.agent.nodes.tool import query_database, query_safety, read_file  # noqa: E402
from app.service.permission import (  # noqa: E402
    data_scope_prompt,
    scoped_region,
    sql_within_scope,
)

REGIONS = ["华东", "华北", "西南", "华南"]

USERS = [
    ("admin      (集团管理员)", {"role": "admin", "region": ""}),
    ("manager_hd (华东区域经理)", {"role": "manager", "region": "华东"}),
    ("viewer     (只读)", {"role": "viewer", "region": ""}),
]


def summarize(text: str) -> str:
    """把工具返回压成一行结论：被拒 / 放行（并标出能看到哪些区域）。"""
    if "不支持" in text or "权限不足" in text:
        return "❌ 拒绝  " + text.strip().strip("（）")[:38]
    hit = [r for r in REGIONS if r in text]
    return "✅ 放行  可见区域 " + (str(hit) if hit else "（无区域维度）")


def demo_tool_matrix():
    print("=" * 78)
    print("第 3 层 出口过滤：同一批工具，不同用户看到的数据范围不同")
    print("=" * 78)
    for name, user in USERS:
        set_current_user(user)
        print(f"\n用户：{name}")
        for label, call in [
            ("query_database()              ", lambda: query_database.invoke({})),
            ("query_safety()                ", lambda: query_safety.invoke({})),
            ("read_file(sync_cost_..md)     ", lambda: read_file.invoke({"filename": "sync_cost_summary.md"})),
            ("read_file(项目成本管理办法.md) ", lambda: read_file.invoke({"filename": "项目成本管理办法.md"})),
            ("read_file(宝冶项目周报..md)   ", lambda: read_file.invoke({"filename": "宝冶项目周报样例.md"})),
        ]:
            print(f"   {label} -> {summarize(call())}")


def demo_sql_guard():
    print("\n" + "=" * 78)
    print("第 1+2 层 SQL 通道：prompt 硬约束 + 执行前静态越权校验")
    print("=" * 78)
    manager = {"role": "manager", "region": "华东"}

    print("\n[manager_hd 的 prompt 约束片段]")
    print("   " + data_scope_prompt(manager).replace("\n", "\n   "))

    cases = [
        ("SELECT region, SUM(amount) FROM cost_records GROUP BY region", "漏写区域过滤 → 越权"),
        ("SELECT SUM(amount) FROM cost_records WHERE region = '华东'", "带本区域过滤 → 合规"),
        ("SELECT SUM(amount) FROM cost_records WHERE region = '华北'", "查别人区域 → 越权"),
    ]
    print("\n[执行前校验 sql_within_scope(sql, manager_hd)]")
    for sql, note in cases:
        ok = sql_within_scope(sql, manager)
        verdict = "✅ 允许执行" if ok else "⛔ 拒绝执行"
        print(f"   {verdict}  ({note})")
        print(f"      {sql}")


def demo_scope_primitive():
    print("\n" + "=" * 78)
    print("作用域原语 scoped_region —— 5 个出口共用的唯一判定（None = 不限区域）")
    print("=" * 78)
    for name, user in USERS:
        r = scoped_region(user["role"], user["region"])
        print(f"   {name:22} -> scoped_region = {r!r}")


if __name__ == "__main__":
    demo_scope_primitive()
    demo_tool_matrix()
    demo_sql_guard()
    print("\n" + "=" * 78)
    print("补充：高危工具 query_business_database 由 APPROVAL_MODE=true 触发人工审批闸门")
    print("      （该闸门按工具复杂度而非角色触发，admin 与 manager 一视同仁）")
    print("=" * 78)
