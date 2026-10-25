import os
import sys

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# 允许 `python app/script/ingest.py` 直接运行：把项目根目录加入 sys.path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from langchain_community.document_loaders import TextLoader
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.conf import config

# 文件名关键词 → 业务域标签（写入 metadata，供多租户/权限过滤与溯源）
_CATEGORY_MAP = {
    "sales": "sales",
    "feedback": "feedback",
    "financial": "finance",
    "product": "product",
    "weather": "weather",
}


# 公司敏感：含全公司/跨区域聚合的业务数据，region 受限角色（sales）不可检索
_SENSITIVE_KEYWORDS = config.COMPANY_SENSITIVE_KEYWORDS


def _infer_metadata(filename: str) -> dict:
    """按文件名推断基础 metadata：文件类型 + 业务域 + 可见性。

    visibility 是 RBAC 的检索层过滤维度：public 任何人可见，
    company 仅非区域受限角色（admin/viewer）可见。
    """
    stem = filename.rsplit(".", 1)[0].lower()
    category = next((v for k, v in _CATEGORY_MAP.items() if k in stem), "general")
    visibility = (
        "company"
        if any(kw in stem for kw in _SENSITIVE_KEYWORDS)
        else "public"
    )
    return {
        "source": filename,
        "file_type": filename.rsplit(".", 1)[-1].lower(),
        "category": category,
        "visibility": visibility,
    }


def load_documents(data_dir: str = "data"):
    """加载 data_dir 下所有 .txt / .md / .csv 文件，统一补全 metadata。"""
    docs = []
    for filename in sorted(os.listdir(data_dir)):
        filepath = os.path.join(data_dir, filename)
        base_meta = _infer_metadata(filename)
        if filename.endswith(('.txt', '.md')):
            loader = TextLoader(filepath, encoding='utf-8')
            for doc in loader.load():
                doc.metadata.update(base_meta)
                docs.append(doc)
            print(f"  加载：{filename}")
        elif filename.endswith('.csv'):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            docs.append(Document(page_content=content, metadata=base_meta))
            print(f"  加载：{filename}")
    return docs


def build_index(data_dir: str = "data", index_path: str = "faiss_index") -> int:
    """完整的 加载 → 切块 → 向量化 → 入库 流水线，可被其他模块调用。

    后端由 config.VECTOR_STORE 决定：qdrant（可达时 upsert 到服务端）或 faiss（本地文件）。
    返回入库的文本块数量。
    """
    print("=== 第1步：加载文档 ===")
    docs = load_documents(data_dir)
    print(f"共加载 {len(docs)} 个文档\n")

    print("=== 第2步：分割文本 ===")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", "。", "！", "？", " ", ""]
    )
    chunks = splitter.split_documents(docs)
    print(f"共分割成 {len(chunks)} 个片段\n")

    print("=== 第3步：向量化 ===")
    embeddings = HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL)

    if config.VECTOR_STORE == "qdrant":
        print("\n=== 第4步：写入 Qdrant ===")
        try:
            from langchain_community.vectorstores import Qdrant
            from qdrant_client import QdrantClient

            client = QdrantClient(url=config.QDRANT_URL, timeout=5)
            Qdrant.from_documents(
                chunks,
                embeddings,
                client=client,
                collection_name=config.QDRANT_COLLECTION,
            )
            print(f"\n✅ 入库完成（Qdrant）！共 {len(chunks)} 个片段")
            # 重建后重置工厂单例，下次检索用新索引
            from app.client.vectorstore import reset_backend

            reset_backend()
            return len(chunks)
        except Exception as e:
            print(f"⚠️ Qdrant 写入失败，回退 FAISS：{e}")

    print("\n=== 第4步：存入 FAISS ===")
    vectorstore = FAISS.from_documents(chunks, embeddings)
    vectorstore.save_local(index_path)
    print(f"\n✅ 入库完成！共 {len(chunks)} 个片段")
    print(f"   索引路径：./{index_path}")

    from app.client.vectorstore import reset_backend

    reset_backend()
    return len(chunks)


def main():
    build_index("data", "faiss_index")


if __name__ == "__main__":
    main()
