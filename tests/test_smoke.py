"""冒烟测试：mock LLM 和向量库，验证工作流各节点和全链路不报错。

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
    """mock 所有外部依赖：LLM、向量库、记忆层。"""
    # mock LLM 调用
    with patch("graph.llm.safe_invoke") as mock_invoke:
        # 根据 prompt 内容返回不同结果，模拟各节点行为
        def fake_invoke(prompt, fallback=""):
            if "分类器" in prompt:
                return "data_analysis"
            if "规划器" in prompt:
                return '["计算总销售额", "分析产品占比"]'
            if "审核" in prompt:
                return "APPROVED"
            if "工具" in prompt:
                return "no_tool"
            # Skill 的回答
            return "总销售额为 2,535,000 元。"

        mock_invoke.side_effect = fake_invoke

        # mock FAISS 向量库
        with patch("graph.nodes.skill.vectorstore") as mock_vs:
            mock_doc = MagicMock()
            mock_doc.page_content = "销售数据片段"
            mock_vs.similarity_search.return_value = [mock_doc]

            # mock Redis 短期记忆
            with patch("memory.short_term.redis_client") as mock_redis:
                mock_redis.get.return_value = None
                mock_redis.setex.return_value = True
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
    """Tool 节点在 no_tool 时不应修改 tool_result。"""
    from graph.nodes.tool import tool_node

    state = {"current_task": "计算销售额", "tool_result": "已有回答"}
    result = tool_node(state)
    assert result["tool_result"] == "已有回答"
    assert result["status"] == "tool_processed"


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
