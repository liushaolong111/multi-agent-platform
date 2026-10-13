"""冒烟测试：mock 所有外部依赖（LLM、向量库、记忆层），验证工作流各节点和全链路。

测试是隔离的：不需要 DEEPSEEK_API_KEY、Redis、PostgreSQL 或 FAISS 索引，
因此可以在 CI 环境中直接运行。

运行方式：
    .venv/Scripts/python.exe -m pytest tests/test_smoke.py -v
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def mock_external_dependencies():
    """mock 所有外部依赖：LLM、Function Calling、向量库、双层记忆。

    注意：节点模块用 `from graph.llm import safe_invoke` 导入，
    因此必须 patch 各节点模块内的引用，而不是 graph.llm 本身。
    """

    def fake_invoke(prompt, fallback=""):
        """根据 prompt 内容模拟各节点的 LLM 返回。"""
        if "分类器" in prompt:
            return "data_analysis"
        if "规划器" in prompt:
            return '["计算总销售额", "分析产品占比"]'
        if "审核" in prompt:
            return "APPROVED"
        # Skill 的回答
        return "总销售额为 2,535,000 元。"

    mock_llm = MagicMock(side_effect=fake_invoke)

    # Function Calling 决策：默认不调用任何工具
    mock_tool_response = MagicMock()
    mock_tool_response.tool_calls = []

    # mock FAISS 向量库
    mock_doc = MagicMock()
    mock_doc.page_content = "销售数据片段"
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [mock_doc]

    with (
        patch("graph.nodes.router.safe_invoke", mock_llm),
        patch("graph.nodes.planner.safe_invoke", mock_llm),
        patch("graph.nodes.skill.safe_invoke", mock_llm),
        patch("graph.nodes.reviewer.safe_invoke", mock_llm),
        patch(
            "graph.nodes.tool.safe_invoke_with_tools",
            return_value=mock_tool_response,
        ),
        patch("graph.nodes.skill.get_vectorstore", return_value=mock_vs),
        patch("graph.nodes.skill.get_session", return_value={}),
        patch("graph.nodes.skill.update_session"),
        patch("graph.nodes.skill.get_long_term", return_value=[]),
        patch("graph.nodes.reviewer.store_long_term"),
    ):
        yield


def test_router_node():
    """Router 应返回合法的 task_type。"""
    from graph.nodes.router import router_node

    state = {"user_input": "分析销售数据"}
    result = router_node(state)
    assert result["task_type"] in {"data_analysis", "report_generation", "knowledge_query"}
    assert result["status"] == "routed"


def test_planner_node():
    """Planner 应返回非空列表。"""
    from graph.nodes.planner import planner_node

    state = {"user_input": "分析销售数据", "task_type": "data_analysis", "iteration": 0}
    result = planner_node(state)
    assert isinstance(result["plan"], list)
    assert len(result["plan"]) > 0
    assert result["status"] == "planned"
    assert result["iteration"] == 1


def test_reviewer_node():
    """Reviewer 应返回 APPROVED 或 REVISE。"""
    from graph.nodes.reviewer import reviewer_node

    state = {"current_task": "计算销售额", "tool_result": "总销售额 253 万", "user_id": "u1", "session_id": "s1"}
    result = reviewer_node(state)
    assert result["review_verdict"] in {"APPROVED", "REVISE"}
    assert result["final_answer"] == "总销售额 253 万"


def test_skill_node_processes_all_tasks():
    """Skill 应遍历 plan 中所有子任务，不只处理第一个。"""
    from graph.nodes.skill import skill_node

    state = {
        "user_input": "分析销售数据",
        "plan": ["计算总销售额", "分析产品占比"],
        "session_id": "s1",
        "user_id": "u1",
    }
    result = skill_node(state)
    # 两个子任务都应出现在结果中
    assert "计算总销售额" in result["tool_result"]
    assert "分析产品占比" in result["tool_result"]
    assert result["status"] == "skill_executed"


def test_tool_node_no_tool():
    """Tool 节点在 LLM 不调用工具时不应修改 tool_result。"""
    from graph.nodes.tool import tool_node

    state = {"current_task": "计算销售额", "tool_result": "已有回答"}
    result = tool_node(state)
    assert result["tool_result"] == "已有回答"
    assert result["status"] == "tool_processed"


def test_tool_node_chart_keyword_triggers_draw():
    """子任务含图表关键词时，应确定性触发绘图，不依赖 LLM 决策。"""
    from graph.nodes.tool import tool_node

    state = {
        "current_task": "绘制月度销售趋势图",
        "plan": ["绘制月度销售趋势图"],
        "tool_result": "已有回答",
    }
    result = tool_node(state)
    assert "[[CHART_BEGIN]]" in result["tool_result"]
    assert "已有回答" in result["tool_result"]


def test_metrics_snapshot_structure():
    """指标模块应能记录并输出快照。"""
    from graph.metrics import Metrics

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


def test_full_graph_end_to_end():
    """全链路：Router → Planner → Skill → Tool → Reviewer 应跑通并返回结果。"""
    from graph.service import graph

    result = graph.invoke(
        {
            "user_input": "分析上季度销售数据",
            "messages": [],
            "status": "start",
            "iteration": 0,
            "session_id": "test_session",
            "user_id": "test_user",
        }
    )
    assert result["task_type"] in {"data_analysis", "report_generation", "knowledge_query"}
    assert isinstance(result["plan"], list)
    assert result["review_verdict"] in {"APPROVED", "REVISE"}
    assert "tool_result" in result or "final_answer" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
