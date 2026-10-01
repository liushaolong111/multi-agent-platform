import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document


def load_documents(data_dir: str = "data"):
    docs = []
    for filename in os.listdir(data_dir):
        filepath = os.path.join(data_dir, filename)
        if filename.endswith(('.txt', '.md')):
            loader = TextLoader(filepath, encoding='utf-8')
            docs.extend(loader.load())
            print(f"  加载：{filename}")
        elif filename.endswith('.csv'):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            docs.append(Document(
                page_content=content,
                metadata={"source": filename}
            ))
            print(f"  加载：{filename}")
    return docs


def main():
    print("=== 第1步：加载文档 ===")
    docs = load_documents("data")
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
    embeddings = HuggingFaceEmbeddings(model_name="BAAI/bge-small-zh-v1.5")

    print("\n=== 第4步：存入 FAISS ===")
    vectorstore = FAISS.from_documents(chunks, embeddings)
    vectorstore.save_local("faiss_index")
    print(f"\n✅ 入库完成！共 {len(chunks)} 个片段")
    print(f"   索引路径：./faiss_index")


if __name__ == "__main__":
    main()