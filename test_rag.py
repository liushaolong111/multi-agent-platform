import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

embeddings = HuggingFaceEmbeddings(model_name="BAAI/bge-small-zh-v1.5")
vectorstore = FAISS.load_local(
    "faiss_index",
    embeddings,
    allow_dangerous_deserialization=True
)

queries = ["销售数据", "产品定价", "客户反馈"]

for query in queries:
    print(f"\n=== 查询：{query} ===")
    results = vectorstore.similarity_search(query, k=2)
    for i, doc in enumerate(results, 1):
        source = doc.metadata.get("source", "未知")
        preview = doc.page_content[:100].replace("\n", " ")
        print(f"  [{i}] 来源：{source}")
        print(f"      内容：{preview}...")