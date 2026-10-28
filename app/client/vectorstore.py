"""向量存储工厂：Qdrant（生产）/ FAISS（本地与降级）双后端统一封装。

设计要点：
- config.VECTOR_STORE=qdrant 时优先连 Qdrant 服务；不可达自动降级 FAISS
  （与 Redis/PG/收集降级同一条"优雅降级"设计主线，CI 无需起任何服务）
- 统一暴露 similarity_search(query, k, filter)：
    Qdrant 后端 → 原生 payload 过滤（服务端执行，真正缩圈）
    FAISS 后端  → 检索后 Python 手动过滤（召回 k*4 再筛，接口对齐）
- 懒加载单例：首次使用时才初始化 embedding 模型与连接，测试可完全 mock
- 多路召回：分桶路由 + dense/sparse 双路 + RRF 融合 + MMR 精排
"""
import logging

import numpy as np

from app.conf import config

logger = logging.getLogger(__name__)

_embeddings = None
_backend = None  # ("qdrant", store) | ("faiss", store)


def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        from langchain_community.embeddings import HuggingFaceEmbeddings

        _embeddings = HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL)
    return _embeddings


def _try_qdrant():
    """尝试连接 Qdrant；失败返回 None（调用方降级 FAISS）。"""
    try:
        from langchain_community.vectorstores import Qdrant
        from qdrant_client import QdrantClient

        client = QdrantClient(url=config.QDRANT_URL, timeout=3)
        client.get_collection(config.QDRANT_COLLECTION)  # 探活：不存在会抛异常
        logger.info(
            "[VectorStore] 已连接 Qdrant：%s/%s",
            config.QDRANT_URL,
            config.QDRANT_COLLECTION,
        )
        return Qdrant(
            client=client,
            collection_name=config.QDRANT_COLLECTION,
            embeddings=_get_embeddings(),
        )
    except Exception as e:
        logger.warning("[VectorStore] Qdrant 不可达，降级 FAISS：%s", e)
        return None


def _load_faiss():
    from langchain_community.vectorstores import FAISS

    return FAISS.load_local(
        config.FAISS_INDEX_DIR,
        _get_embeddings(),
        allow_dangerous_deserialization=True,
    )


def get_backend():
    """获取 (backend_name, store) 单例；VECTOR_STORE=qdrant 且可达时用 Qdrant。"""
    global _backend
    if _backend is None:
        store = _try_qdrant() if config.VECTOR_STORE == "qdrant" else None
        if store is not None:
            _backend = ("qdrant", store)
        else:
            _backend = ("faiss", _load_faiss())
        logger.info("[VectorStore] 当前后端：%s", _backend[0])
    return _backend


def get_vectorstore():
    """返回原生 store 对象（skill.py 及测试 mock 的兼容入口）。"""
    return get_backend()[1]


def similarity_search(query: str, k: int, metadata_filter: dict | None = None):
    """统一检索入口：Qdrant 走原生 filter，FAISS 走召回后过滤。

    Args:
        query: 检索文本
        k: 返回条数
        metadata_filter: 等值过滤条件，如 {"visibility": "public"}
    """
    backend, store = get_backend()
    if not metadata_filter:
        return store.similarity_search(query, k=k)

    if backend == "qdrant":
        # 服务端原生过滤：先按 payload 缩圈再做向量排序
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        qdrant_filter = Filter(
            must=[
                FieldCondition(key=key, match=MatchValue(value=value))
                for key, value in metadata_filter.items()
            ]
        )
        return store.similarity_search(query, k=k, filter=qdrant_filter)

    # FAISS 降级：放大召回后在 Python 侧过滤，保证接口语义一致
    candidates = store.similarity_search(query, k=k * 4)
    matched = [
        doc
        for doc in candidates
        if all(doc.metadata.get(key) == value for key, value in metadata_filter.items())
    ]
    return matched[:k]


def reset_backend():
    """重置单例（测试与索引重建后调用）。"""
    global _backend, _bm25_index, _all_docs
    _backend = None
    _bm25_index = None
    _all_docs = None


# ===================== 多路召回：分桶路由 + 混合检索 =====================

_bm25_index = None
_all_docs = None

# 查询关键词 → 业务域（用于分桶路由）
_DOMAIN_KEYWORDS = {
    "cost": ["成本", "预算", "费用", "金额", "合同额", "造价", "支出"],
    "safety": ["安全", "隐患", "事故", "防护", "高处", "用电", "临边", "洞口"],
    "quality": ["质量", "缺陷", "验收", "强度", "混凝土", "钢筋", "焊缝"],
    "technical": ["技术", "施工", "工艺", "规范", "方案", "交底"],
    "management": ["合同", "进度", "管理", "审批", "流程", "制度"],
    "material": ["材料", "库存", "供应", "到货", "构件", "钢结构", "预制", "采购", "仓储"],
    "weather": ["天气", "降雨", "温度", "雨季", "台风"],
}


def _infer_domain(query: str) -> str:
    """根据查询关键词推断业务域，用于分桶路由。"""
    q = query.lower()
    scores = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        scores[domain] = sum(1 for kw in keywords if kw in q)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def _build_bm25():
    """从 FAISS docstore 加载所有文档，构建 BM25 索引（懒加载，只构建一次）。"""
    global _bm25_index, _all_docs
    if _bm25_index is not None:
        return

    from rank_bm25 import BM25Okapi

    # 从 FAISS 索引的 docstore 读取所有文档
    store = _load_faiss()
    all_docs = list(store.docstore._dict.values())
    _all_docs = all_docs

    # 中文分词：按字切分（简单有效，无需额外分词依赖）
    tokenized = [list(doc.page_content) for doc in all_docs]
    _bm25_index = BM25Okapi(tokenized)
    logger.info("[MultiRoute] BM25 索引构建完成，共 %d 个文档片段", len(all_docs))


def _sparse_search(query: str, k: int, domain: str | None = None):
    """BM25 关键词检索，可选按 business_domain 过滤。"""
    _build_bm25()
    if _bm25_index is None:
        return []

    # 中文按字分词查询
    tokenized_query = list(query)
    scores = _bm25_index.get_scores(tokenized_query)

    # 按分数降序，过滤业务域
    ranked = sorted(
        range(len(_all_docs)),
        key=lambda i: scores[i],
        reverse=True,
    )
    results = []
    for idx in ranked:
        if scores[idx] <= 0:
            break
        doc = _all_docs[idx]
        if domain and doc.metadata.get("business_domain") != domain:
            continue
        results.append(doc)
        if len(results) >= k:
            break
    return results


def _rrf_fuse(dense_docs, sparse_docs, k: int, rrf_k: int = 60):
    """Reciprocal Rank Fusion：融合 dense 与 sparse 两路结果。

    RRF 公式：score(d) = Σ 1 / (rrf_k + rank_i(d))
    无需归一化两路分数，直接用排名融合，鲁棒性好。
    """
    fused = {}
    for rank, doc in enumerate(dense_docs):
        key = id(doc)
        fused[key] = fused.get(key, 0) + 1.0 / (rrf_k + rank + 1)
    for rank, doc in enumerate(sparse_docs):
        key = id(doc)
        fused[key] = fused.get(key, 0) + 1.0 / (rrf_k + rank + 1)

    # 去重并按融合分数排序（dense_docs 在前保证同分时 dense 优先）
    seen = set()
    ordered = []
    for doc in dense_docs + sparse_docs:
        if id(doc) not in seen:
            seen.add(id(doc))
            ordered.append(doc)
    ordered.sort(key=lambda d: fused.get(id(d), 0), reverse=True)
    return ordered[:k]


def _cosine_sim(a, b):
    """余弦相似度，数值稳定版。"""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom < 1e-8:
        return 0.0
    return float(np.dot(a, b) / denom)


def _mmr_rerank(query: str, docs, k: int, lambda_param: float = 0.7):
    """MMR（Maximal Marginal Relevance）精排：在相关性与多样性之间取平衡。

    score(d) = λ * rel(d, q) - (1-λ) * max_{s∈S} sim(d, s)
    - rel(d, q)：文档与查询的语义相似度（embedding 余弦）
    - sim(d, s)：文档与已选文档的最大相似度（避免重复）
    - λ=1 退化为纯相关性排序，λ=0 退化为纯多样性

    Args:
        query: 查询文本
        docs: 候选文档列表（RRF 融合后的结果）
        k: 最终返回条数
        lambda_param: 相关性权重，默认 0.7
    """
    if len(docs) <= k:
        return docs

    embeddings = _get_embeddings()
    query_vec = embeddings.embed_query(query)
    doc_vecs = embeddings.embed_documents([d.page_content for d in docs])

    # 相关性分数
    rel_scores = [_cosine_sim(query_vec, dv) for dv in doc_vecs]

    selected_indices = []
    candidates = set(range(len(docs)))

    while len(selected_indices) < k and candidates:
        best_idx = None
        best_score = -float("inf")
        for idx in candidates:
            relevance = rel_scores[idx]
            # 与已选文档的最大相似度
            if selected_indices:
                max_sim = max(_cosine_sim(doc_vecs[idx], doc_vecs[s]) for s in selected_indices)
            else:
                max_sim = 0.0
            score = lambda_param * relevance - (1 - lambda_param) * max_sim
            if score > best_score:
                best_score = score
                best_idx = idx
        selected_indices.append(best_idx)
        candidates.remove(best_idx)

    return [docs[i] for i in selected_indices]


def multi_route_search(
    query: str, k: int = 3, metadata_filter: dict | None = None
):
    """多路召回入口：分桶路由 + dense/sparse 混合检索 + RRF 融合 + MMR 精排。

    流程：
    1. 推断查询业务域（business_domain），优先在该域内检索
    2. dense 检索（FAISS 向量相似度）+ sparse 检索（BM25 关键词）
    3. RRF 融合两路结果（取 RERANK_CANDIDATES 条候选）
    4. MMR 精排（平衡相关性与多样性，取 k 条）
    5. 应用 metadata_filter（visibility 等 RBAC 过滤）

    Args:
        query: 检索文本
        k: 返回条数
        metadata_filter: 等值过滤条件（如 {"visibility": "public"}）
    """
    domain = _infer_domain(query)
    logger.info("[MultiRoute] 查询='%s'，推断业务域=%s", query[:30], domain)

    # 分桶路由：先在目标域内检索，不足再回退全量
    fetch_k = max(k * 3, config.RERANK_CANDIDATES)
    dense_docs = similarity_search(query, k=fetch_k, metadata_filter={"business_domain": domain}) if domain != "general" else []
    sparse_docs = _sparse_search(query, k=fetch_k, domain=domain if domain != "general" else None)

    # 目标域结果不足时，回退全量检索补充
    if len(dense_docs) < k:
        dense_docs += similarity_search(query, k=fetch_k)
    if len(sparse_docs) < k:
        sparse_docs += _sparse_search(query, k=fetch_k)

    # RRF 融合（粗排）
    fused = _rrf_fuse(dense_docs, sparse_docs, k=config.RERANK_CANDIDATES)

    # MMR 精排（平衡相关性与多样性）
    reranked = _mmr_rerank(query, fused, k=k, lambda_param=config.MMR_LAMBDA)

    # 应用 RBAC 等过滤
    if metadata_filter:
        reranked = [
            doc
            for doc in reranked
            if all(doc.metadata.get(key) == value for key, value in metadata_filter.items())
        ]

    return reranked[:k]
