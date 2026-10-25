"""向量存储工厂：Qdrant（生产）/ FAISS（本地与降级）双后端统一封装。

设计要点：
- config.VECTOR_STORE=qdrant 时优先连 Qdrant 服务；不可达自动降级 FAISS
  （与 Redis/PG/收集降级同一条"优雅降级"设计主线，CI 无需起任何服务）
- 统一暴露 similarity_search(query, k, filter)：
    Qdrant 后端 → 原生 payload 过滤（服务端执行，真正缩圈）
    FAISS 后端  → 检索后 Python 手动过滤（召回 k*4 再筛，接口对齐）
- 懒加载单例：首次使用时才初始化 embedding 模型与连接，测试可完全 mock
"""
import logging

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
        metadata_filter: 等值过滤条件，如 {"department": "sales"}
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
    global _backend
    _backend = None
