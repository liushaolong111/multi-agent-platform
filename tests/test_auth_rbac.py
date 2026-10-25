"""Phase 2 单元测试：JWT 认证、RBAC 数据权限、向量库工厂降级。

完全 hermetic：不依赖真实数据库、Qdrant 服务与 LLM。
    .venv/Scripts/python.exe -m pytest tests/test_auth_rbac.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---- JWT ----

def test_jwt_issue_and_decode_roundtrip():
    from app.api.auth import decode_token, issue_token

    user = {"user_id": "sales_hd", "role": "sales", "region": "华东"}
    token = issue_token(user)
    decoded = decode_token(token)
    assert decoded["user_id"] == "sales_hd"
    assert decoded["role"] == "sales"
    assert decoded["region"] == "华东"


def test_jwt_decode_rejects_tampered_token():
    from fastapi import HTTPException

    from app.api.auth import decode_token, issue_token

    token = issue_token({"user_id": "u", "role": "sales", "region": "华东"})
    bad = token[:-3] + "xxx"
    with pytest.raises(HTTPException) as exc_info:
        decode_token(bad)
    assert exc_info.value.status_code == 401


def test_jwt_decode_rejects_expired(monkeypatch):
    from fastapi import HTTPException

    from app.api.auth import decode_token, issue_token
    from app.conf import config

    monkeypatch.setattr(config, "JWT_EXPIRE_MINUTES", -1)  # 已过期
    token = issue_token({"user_id": "u", "role": "admin", "region": ""})
    with pytest.raises(HTTPException) as exc_info:
        decode_token(token)
    assert exc_info.value.status_code == 401


# ---- 密码哈希 ----

def test_password_hash_and_verify():
    from app.repository.users import hash_password, verify_password

    stored = hash_password("secret123")
    assert verify_password("secret123", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("secret123", "malformed")


# ---- RBAC 数据权限 ----

SALES_USER = {"user_id": "sales_hd", "role": "sales", "region": "华东"}
ADMIN_USER = {"user_id": "admin", "role": "admin", "region": ""}


def test_scope_prompt_only_for_sales():
    from app.service.permission import data_scope_prompt

    assert "华东" in data_scope_prompt(SALES_USER)
    assert data_scope_prompt(ADMIN_USER) == ""


def test_sql_within_scope_allows_region_filtered():
    from app.service.permission import sql_within_scope

    ok_sql = "SELECT SUM(amount) FROM sales_records WHERE region = '华东' GROUP BY product"
    assert sql_within_scope(ok_sql, SALES_USER)
    # admin 不受限
    assert sql_within_scope("SELECT SUM(amount) FROM sales_records", ADMIN_USER)


def test_sql_within_scope_blocks_unfiltered_and_other_region():
    from app.service.permission import sql_within_scope

    # 没带区域过滤 → 拦
    assert not sql_within_scope("SELECT SUM(amount) FROM sales_records", SALES_USER)
    # 查别人的区域 → 拦
    assert not sql_within_scope(
        "SELECT * FROM sales_records WHERE region = '华北'", SALES_USER
    )
    # 不碰 sales_records 的查询（如 feedback）→ 放行
    assert sql_within_scope("SELECT AVG(rating) FROM feedback_records", SALES_USER)


def test_text2sql_tool_blocks_out_of_scope(monkeypatch):
    """LLM 生成越权 SQL 时，工具应在执行前拦截（不打真库）。"""
    import app.agent.nodes.text2sql as t2s

    monkeypatch.setattr(
        t2s, "generate_sql", lambda q, u=None: "SELECT SUM(amount) FROM sales_records"
    )
    t2s.set_current_user(SALES_USER)
    result = t2s.query_business_database.invoke({"question": "总销售额多少"})
    assert "权限不足" in result
    t2s.set_current_user(ADMIN_USER)  # 复位


# ---- 向量库工厂 ----

def test_vectorstore_factory_falls_back_to_faiss(monkeypatch):
    """VECTOR_STORE=qdrant 但服务不可达时，应自动降级 FAISS。"""
    import app.client.vectorstore as vs
    from app.conf import config

    monkeypatch.setattr(config, "VECTOR_STORE", "qdrant")
    monkeypatch.setattr(vs, "_try_qdrant", lambda: None)  # 模拟不可达
    fake_faiss = object()
    monkeypatch.setattr(vs, "_load_faiss", lambda: fake_faiss)
    vs.reset_backend()

    backend, store = vs.get_backend()
    assert backend == "faiss"
    assert store is fake_faiss
    vs.reset_backend()


def test_similarity_search_post_filters_on_faiss(monkeypatch):
    """FAISS 后端的 metadata 过滤：召回后按 filter 筛选到 k。"""
    import app.client.vectorstore as vs

    class FakeDoc:
        def __init__(self, src):
            self.metadata = {"source": src}
            self.page_content = "x"

    docs = [FakeDoc("a.md"), FakeDoc("b.md"), FakeDoc("a.md"), FakeDoc("c.md")]

    class FakeStore:
        def similarity_search(self, query, k):
            return docs[:k]

    monkeypatch.setattr(vs, "_backend", ("faiss", FakeStore()))
    result = vs.similarity_search("q", k=2, metadata_filter={"source": "a.md"})
    assert len(result) == 2
    assert all(d.metadata["source"] == "a.md" for d in result)
    vs.reset_backend()
