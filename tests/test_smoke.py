"""冒烟测试：mock 所有外部依赖（LLM、向量库、记忆层），验证工作流各节点和全链路。

测试是隔离的：不需要 DEEPSEEK_API_KEY、Redis、PostgreSQL、Qdrant 或 FAISS 索引，
因此可以在 CI 环境中直接运行。

运行方式：
    .venv/Scripts/python.exe -m pytest tests/test_smoke.py -v
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fake_llm_reply(prompt, fallback=""):
    """根据 prompt 内容模拟各节点的 LLM 返回。"""
    if "分类器" in prompt:
        return "data_analysis"
    if "规划器" in prompt:
        return '["计算总销售额", "分析产品占比"]'
    if "审核" in prompt:
        return "APPROVED"
    # Skill 的回答
    return "总销售额为 2,535,000 元。"


@pytest.fixture(autouse=True)
def mock_external_dependencies():
    """mock 所有外部依赖：LLM（含异步路径）、Function Calling、向量库、双层记忆。

    注意：节点模块用 `from app.agent.llm import ...` 导入，
    因此必须 patch 各节点模块内的引用，而不是 app.agent.llm 本身。
    """
    mock_llm = MagicMock(side_effect=_fake_llm_reply)
    # 异步 LLM（skill 并发子任务用 safe_ainvoke）
    mock_alm = AsyncMock(side_effect=_fake_llm_reply)

    # Function Calling 决策：默认不调用任何工具
    mock_tool_response = MagicMock()
    mock_tool_response.tool_calls = []

    # mock 向量检索（skill 走 app.client.vectorstore.similarity_search 统一入口）
    mock_doc = MagicMock()
    mock_doc.page_content = "销售数据片段"

    with (
        patch("app.agent.nodes.router.safe_invoke", mock_llm),
        patch("app.agent.nodes.planner.safe_invoke", mock_llm),
        patch("app.agent.nodes.skill.safe_ainvoke", mock_alm),
        patch("app.agent.nodes.reviewer.safe_invoke", mock_llm),
        patch(
            "app.agent.nodes.tool_decide.safe_ainvoke_with_tools",
            new=AsyncMock(return_value=mock_tool_response),
        ),
        patch("app.agent.nodes.skill.similarity_search", return_value=[mock_doc]),
        patch("app.agent.nodes.skill.get_session", return_value={}),
        patch("app.agent.nodes.skill.update_session"),
        patch("app.agent.nodes.skill.get_long_term", return_value=[]),
        patch("app.agent.nodes.reviewer.store_long_term"),
    ):
        yield


def test_router_node():
    """Router 应返回合法的 task_type。"""
    from app.agent.nodes.router import router_node

    state = {"user_input": "分析销售数据"}
    result = router_node(state)
    assert result["task_type"] in {"data_analysis", "report_generation", "knowledge_query"}
    assert result["status"] == "routed"


def test_planner_node():
    """Planner 应返回非空列表。"""
    from app.agent.nodes.planner import planner_node

    state = {"user_input": "分析销售数据", "task_type": "data_analysis", "iteration": 0}
    result = planner_node(state)
    assert isinstance(result["plan"], list)
    assert len(result["plan"]) > 0
    assert result["status"] == "planned"
    assert result["iteration"] == 1


def test_reviewer_node():
    """Reviewer 应返回 APPROVED 或 REVISE。"""
    from app.agent.nodes.reviewer import reviewer_node

    state = {"current_task": "计算销售额", "tool_result": "总销售额 253 万", "user_id": "u1", "session_id": "s1"}
    result = reviewer_node(state)
    assert result["review_verdict"] in {"APPROVED", "REVISE"}
    assert result["final_answer"] == "总销售额 253 万"


def test_skill_node_processes_all_tasks():
    """Skill 应遍历 plan 中所有子任务，不只处理第一个。"""
    from app.agent.nodes.skill import skill_node

    state = {
        "user_input": "分析销售数据",
        "plan": ["计算总销售额", "分析产品占比"],
        "session_id": "s1",
        "user_id": "u1",
    }
    result = asyncio.run(skill_node(state))
    # 两个子任务都应出现在结果中
    assert "计算总销售额" in result["tool_result"]
    assert "分析产品占比" in result["tool_result"]
    assert result["status"] == "skill_executed"


def test_tool_decide_no_tool():
    """ReAct 决策：LLM 不调工具时 pending 为空（收敛）。"""
    from app.agent.nodes.tool_decide import tool_decide_node

    state = {"current_task": "计算销售额", "tool_result": "已有回答", "tool_rounds": 0}
    result = asyncio.run(tool_decide_node(state))
    assert result["pending_tool_calls"] == []


def test_tool_decide_chart_keyword_triggers_draw():
    """子任务含图表关键词时，首轮应确定性触发绘图，不依赖 LLM 决策。"""
    from app.agent.nodes.tool_decide import tool_decide_node

    state = {
        "current_task": "绘制月度销售趋势图",
        "plan": ["绘制月度销售趋势图"],
        "tool_result": "已有回答",
        "tool_rounds": 0,
    }
    result = asyncio.run(tool_decide_node(state))
    assert "[[CHART_BEGIN]]" in result["tool_result"]
    assert "已有回答" in result["tool_result"]


def test_tool_exec_accumulates_observations():
    """ReAct 执行：工具结果写入观测，轮数递增，收尾时合并进 tool_result。"""
    from app.agent.nodes.tool_exec import tool_exec_node

    fake_tool = MagicMock()
    fake_tool.invoke.return_value = "查询结果：华东 100 万"
    with patch("app.agent.nodes.tool_exec._TOOL_MAP", {"query_database": fake_tool}):
        state = {
            "current_task": "查销售额",
            "pending_tool_calls": [{"name": "query_database", "args": {}}],
            "tool_observations": "",
            "tool_rounds": 0,
            "tool_result": "已有回答",
            "user_id": "u1",
        }
        result = tool_exec_node(state)

    assert result["tool_rounds"] == 1
    assert result["last_tool_executed"] == 1
    assert "华东 100 万" in result["tool_observations"]
    assert result["pending_tool_calls"] == []


def test_react_route_edges():
    """ReAct 条件边：有调用→执行、无调用→审核、执行后未超轮数→回决策。"""
    from app.agent.nodes.tool_decide import route_after_decide
    from app.agent.nodes.tool_exec import route_after_exec
    from app.conf import config

    assert route_after_decide({"pending_tool_calls": [{"name": "x", "args": {}}]}) == "execute"
    assert route_after_decide({"pending_tool_calls": []}) == "finish"
    assert route_after_exec({"last_tool_executed": 1, "tool_rounds": 1}) == "continue"
    assert route_after_exec({"last_tool_executed": 0, "tool_rounds": 1}) == "finish"
    # 达到最大轮数强制收尾
    assert route_after_exec(
        {"last_tool_executed": 1, "tool_rounds": config.MAX_TOOL_ROUNDS}
    ) == "finish"


def test_metrics_snapshot_structure():
    """指标模块应能记录并输出快照。"""
    from app.agent.metrics import Metrics

    m = Metrics()
    m.record_request()
    m.record_llm_call(120.0, prompt_tokens=10, completion_tokens=20)
    m.record_node("router", 50.0)
    m.record_tool("query_database")
    snap = m.snapshot()
    assert snap["requests"]["total"] == 1
    assert snap["llm"]["total_tokens"] == 30
    assert snap["nodes"]["router"]["count"] == 1
    assert snap["tool_calls"]["query_database"] == 1


def test_scoped_region_and_rag_filter():
    """RBAC 作用域原语：region 受限角色才有区域作用域与检索过滤条件。"""
    from app.service.permission import rag_scope_filter, scoped_region

    assert scoped_region("sales", "华东") == "华东"
    assert scoped_region("admin", "") is None
    assert scoped_region("viewer", "") is None
    # sales 未配区域时不限制（避免误锁死）
    assert scoped_region("sales", "") is None

    assert rag_scope_filter("sales", "华东") == {"visibility": "public"}
    assert rag_scope_filter("admin", "") is None


def test_skill_scopes_retrieval_for_sales():
    """region 受限角色检索必须带 visibility=public 过滤（堵住 RAG 旁路泄露）。"""
    from app.agent.nodes.skill import skill_node

    captured = {}

    def fake_search(query, k, metadata_filter=None):
        captured["metadata_filter"] = metadata_filter
        return [MagicMock(page_content="公开资料")]

    with patch("app.agent.nodes.skill.similarity_search", side_effect=fake_search):
        state = {
            "user_input": "各区域销售额",
            "plan": ["汇总销售额"],
            "session_id": "s1",
            "user_id": "sales_hd",
            "user_role": "sales",
            "user_region": "华东",
        }
        asyncio.run(skill_node(state))

    assert captured["metadata_filter"] == {"visibility": "public"}


def test_tool_exports_respect_scope():
    """工具出口权限：region 受限角色不能读公司级文件、不能查公司级反馈。"""
    from app.agent.nodes.text2sql import _current_user, set_current_user
    from app.agent.nodes.tool import query_feedback, read_file

    try:
        set_current_user({"role": "sales", "region": "华东"})
        assert "不支持读取" in read_file.invoke({"filename": "sales_q1.csv"})
        assert "不支持查询" in query_feedback.invoke({})

        set_current_user({"role": "admin", "region": ""})
        # admin 不受限：文件存在则正常返回内容
        assert "不支持" not in read_file.invoke({"filename": "product_info.md"})
    finally:
        _current_user.set({"role": "admin", "region": ""})


def test_full_graph_end_to_end():
    """全链路：Router → Planner → Skill → ReAct → Reviewer 应跑通并返回结果。"""
    from app.agent.service import graph

    result = asyncio.run(
        graph.ainvoke(
            {
                "user_input": "分析上季度销售数据",
                "messages": [],
                "status": "start",
                "iteration": 0,
                "session_id": "test_session",
                "user_id": "test_user",
            },
            config={"configurable": {"thread_id": "test_e2e_smoke"}},
        )
    )
    assert result["task_type"] in {"data_analysis", "report_generation", "knowledge_query"}
    assert isinstance(result["plan"], list)
    assert result["review_verdict"] in {"APPROVED", "REVISE"}
    assert "tool_result" in result or "final_answer" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
