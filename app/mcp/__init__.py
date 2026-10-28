"""MCP 工具总线：将所有外部能力以 MCP Server 形式标准化暴露。

Agent 可通过统一的 tool_call 接口调用，实现解耦与可替换。
当前 demo 中，LangChain Function Calling 链路继续工作，
本 MCP Server 作为"能力标准化出口"并行存在，支持任意 MCP 客户端接入。
"""
