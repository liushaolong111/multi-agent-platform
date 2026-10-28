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

# 文件名关键词 → 业务域标签（写入 metadata，供分桶路由与溯源）
_BUSINESS_DOMAIN_MAP = {
    "成本": "cost",
    "cost": "cost",
    "安全": "safety",
    "safety": "safety",
    "高处": "safety",
    "用电": "safety",
    "质量": "quality",
    "混凝土": "quality",
    "施工技术": "technical",
    "钢筋": "technical",
    "合同": "management",
    "进度": "management",
    "材料": "material",
    "库存": "material",
    "供应": "material",
    "采购": "material",
    "weather": "weather",
}

# 文件名关键词 → 文档类型（制度/规范/指导书/报告/数据）
_DOC_TYPE_MAP = {
    "办法": "regulation",
    "制度": "regulation",
    "规范": "specification",
    "标准": "specification",
    "指导书": "guide",
    "周报": "report",
    "报告": "report",
    "summary": "data",
    "weather": "data",
}


# 公司敏感：含全公司/跨区域聚合的业务数据，region 受限角色（manager）不可检索
_SENSITIVE_KEYWORDS = config.COMPANY_SENSITIVE_KEYWORDS

# 结构化业务数据：由 Text-to-SQL 与预计算统计负责查询。
# 不能进向量库——原始行若以 public 可见性入库，manager 可通过 RAG 检索到其他区域明细，
# 等于绕过了区域行级隔离。知识库只放"制度/摘要"这类无行级明细的文本。
_EXCLUDED_FILES = {"projects.csv", "cost_records.csv", "safety_issues.csv", "material_records.csv"}


def _infer_metadata(filename: str) -> dict:
    """按文件名推断 metadata：业务域 + 文档类型 + 时间 + 可见性。

    新增字段说明（多路召回用）：
    - business_domain：业务域，用于分桶路由（cost/safety/quality/technical/management/weather/general）
    - doc_type：文档类型，用于过滤与排序（regulation/specification/guide/report/data/general）
    - time_period：适用时间周期（all / 2026H1 等），当前制度类文档默认 all
    - visibility：RBAC 检索层过滤维度（public / company）
    """
    stem = filename.rsplit(".", 1)[0].lower()
    business_domain = next(
        (v for k, v in _BUSINESS_DOMAIN_MAP.items() if k in stem), "general"
    )
    doc_type = next(
        (v for k, v in _DOC_TYPE_MAP.items() if k in stem), "general"
    )
    # 时间周期：制度类文档长期有效，标记为 all；数据摘要可从文件名推断
    time_period = "all"
    if "2026" in stem:
        time_period = "2026H1"
    visibility = (
        "company"
        if any(kw in stem for kw in _SENSITIVE_KEYWORDS)
        else "public"
    )
    return {
        "source": filename,
        "file_type": filename.rsplit(".", 1)[-1].lower(),
        "business_domain": business_domain,
        "doc_type": doc_type,
        "time_period": time_period,
        "visibility": visibility,
    }


def load_documents(data_dir: str = "data"):
    """加载 data_dir 下所有 .txt / .md / .csv 文件，统一补全 metadata。"""
    docs = []
    for filename in sorted(os.listdir(data_dir)):
        if filename in _EXCLUDED_FILES:
            print(f"  跳过（结构化业务数据，走 SQL 查询）：{filename}")
            continue
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
