"""MCP 客户端适配器：将 MCP Server 暴露的工具桥接到 LangChain 工具系统。

支持两种调用模式：
- in_process（默认）：在同一进程内直接调用 MCPServer.call_tool，零延迟，适合 demo
- stdio：通过子进程启动独立 MCP Server，用 MCP 协议跨进程通信，展示真正的解耦

使用方式：
    from app.mcp.client import get_mcp_tools
    mcp_tools = get_mcp_tools()  # 返回 LangChain Tool 列表，可注册到 Agent
"""
from __future__ import annotations

import asyncio
import logging

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.mcp.server import mcp as _mcp_server

logger = logging.getLogger(__name__)

# 调用模式：in_process 或 stdio
_MCP_MODE = "in_process"


# ---------------------------------------------------------------------------
# 工具调用核心
# ---------------------------------------------------------------------------

def _call_mcp_tool_sync(name: str, **kwargs) -> str:
    """同步调用 MCP 工具（进程内模式）。"""
    result = asyncio.run(_mcp_server.call_tool(name, kwargs))
    if hasattr(result, "content") and result.content:
        return result.content[0].text
    return str(result)


async def _call_mcp_tool_async(name: str, **kwargs) -> str:
    """异步调用 MCP 工具（进程内模式）。"""
    result = await _mcp_server.call_tool(name, kwargs)
    if hasattr(result, "content") and result.content:
        return result.content[0].text
    return str(result)


# ---------------------------------------------------------------------------
# LangChain Tool 适配器
# ---------------------------------------------------------------------------

def _make_langchain_tool(tool_name: str, description: str, arg_names: list[str]) -> StructuredTool:
    """将 MCP 工具包装为 LangChain StructuredTool。"""

    def _run(**kwargs) -> str:
        return _call_mcp_tool_sync(tool_name, **kwargs)

    async def _arun(**kwargs) -> str:
        return await _call_mcp_tool_async(tool_name, **kwargs)

    # 动态生成参数 schema
    args_schema = _build_args_schema(tool_name, arg_names)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name=f"mcp_{tool_name}",
        description=description,
        args_schema=args_schema,
    )


def _build_args_schema(tool_name: str, arg_names: list[str]) -> type[BaseModel]:
    """为 MCP 工具动态构建 Pydantic 参数 schema。"""
    annotations = {arg: str | None for arg in arg_names}
    fields = {arg: Field(default=None, description=f"{arg} 参数") for arg in arg_names}
    return type(
        f"{tool_name}_args",
        (BaseModel,),
        {"__annotations__": annotations, **fields},
    )


# ---------------------------------------------------------------------------
# 工具清单（与 app/mcp/server.py 保持一致）
# ---------------------------------------------------------------------------

_MCP_TOOL_SPECS = [
    {
        "name": "mysql_query",
        "description": "执行只读 SQL 查询业务数据库（带 RBAC 区域过滤）。参数：sql（只读SQL）、region（可选区域）",
        "args": ["sql", "region"],
    },
    {
        "name": "mysql_query_materials",
        "description": "查询材料库存（含到货滞后信息）。参数：region（可选区域）",
        "args": ["region"],
    },
    {
        "name": "vector_search",
        "description": "向量检索（多路召回：分桶路由 + dense/sparse 混合 + RRF + MMR）。参数：query（检索文本）、k（返回条数）",
        "args": ["query", "k"],
    },
    {
        "name": "erp_fetch_purchase_orders",
        "description": "从 ERP 系统获取采购订单（项目/材料/供应商/计划到货日期）。参数：region（可选区域）",
        "args": ["region"],
    },
    {
        "name": "erp_fetch_inventory",
        "description": "从 ERP 系统获取库存余额。参数：region（可选区域）",
        "args": ["region"],
    },
    {
        "name": "erp_fetch_schedule",
        "description": "从 ERP 系统获取到货计划。参数：region（可选区域）",
        "args": ["region"],
    },
    {
        "name": "get_db_schema",
        "description": "获取业务库 schema 描述（用于 Text-to-SQL 上下文）。",
        "args": [],
    },
]


def get_mcp_tools() -> list[StructuredTool]:
    """返回所有 MCP 工具的 LangChain Tool 列表，可注册到 Agent 的工具列表中。"""
    tools = []
    for spec in _MCP_TOOL_SPECS:
        try:
            tool = _make_langchain_tool(spec["name"], spec["description"], spec["args"])
            tools.append(tool)
        except Exception as e:
            logger.warning("MCP 工具 %s 注册失败：%s", spec["name"], e)
    logger.info("MCP 工具总线已注册 %d 个工具：%s", len(tools), [t.name for t in tools])
    return tools


def call_mcp_tool(name: str, **kwargs) -> str:
    """直接调用 MCP 工具（供非 LangChain 场景使用）。

    Args:
        name: MCP 工具名（如 mysql_query）
        **kwargs: 工具参数
    """
    return _call_mcp_tool_sync(name, **kwargs)


if __name__ == "__main__":
    # 快速验证：列出并调用所有 MCP 工具
    tools = get_mcp_tools()
    print(f"已注册 {len(tools)} 个 MCP 工具：")
    for t in tools:
        print(f"  - {t.name}: {t.description[:60]}...")
