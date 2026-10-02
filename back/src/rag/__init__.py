"""RAG 模块：文档解析（pypdfium2）→ 切分（langchain-text-splitters）→ 嵌入 → Chroma 入库与检索。

本模块不依赖任何 langchain-community 组件（该包已 sunset 归档）：
- 解析：pypdfium2 逐页提取 PDF 文本（metadata.source + metadata.page）；TXT / Markdown
  直接读取（UTF-8 优先，回退 GBK）
- 切分：RecursiveCharacterTextSplitter（中文分隔符优先）
- 存储：langchain-chroma，持久化到 settings.chroma_dir

对外接口：
- load_document / load_directory：单文件 / 目录批量解析为 Document 列表
- split_documents：按 settings.chunk_size / chunk_overlap 切分
- RagService：add_documents / add_directory / search / list_documents / delete_document / clear
- get_rag_service：进程内单例（API 路由与 Agent 工具共用同一实例）

均为同步实现（pypdfium2 与 Chroma 是同步库），API 层用 asyncio.to_thread 包装调用。
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings
from llm import get_embeddings

__all__: list[str] = [
    "SUPPORTED_SUFFIXES",
    "RagService",
    "get_rag_service",
    "load_directory",
    "load_document",
    "split_documents",
]

logger = logging.getLogger(__name__)

# 支持解析的文件类型（后续阶段按需扩展 docx / csv 等）
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".markdown"}

# 中文优先的切分分隔符：段落 → 换行 → 句末标点 → 子句标点 → 空格 → 逐字符
SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def _read_text(path: Path) -> str:
    """读取文本文件，UTF-8 优先，失败回退 GBK（Windows 常见编码）。"""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gbk")


def _parse_pdf(path: Path) -> list[Document]:
    """用 pypdfium2 逐页解析 PDF，空白页跳过；page 从 1 开始（与阅读器一致）。"""
    documents: list[Document] = []
    with pdfium.PdfDocument(str(path)) as pdf:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range().strip()
            finally:
                textpage.close()
                page.close()
            if not text:
                continue
            documents.append(
                Document(
                    page_content=text,
                    metadata={"source": str(path), "page": page_index + 1},
                )
            )
    return documents


def load_document(path: str | Path) -> list[Document]:
    """解析单个文件（.pdf / .txt / .md）为 Document 列表。

    metadata：source（文件路径）；PDF 另有 page（页码，从 1 开始）。

    Raises:
        ValueError: 文件类型不支持。
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(file_path)
    if suffix in SUPPORTED_SUFFIXES:
        text = _read_text(file_path).strip()
        if not text:
            return []
        return [Document(page_content=text, metadata={"source": str(file_path)})]
    supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
    raise ValueError(f"不支持的文件类型：{suffix}（支持 {supported}）")


def load_directory(directory: str | Path | None = None) -> list[Document]:
    """批量解析目录下所有受支持文件（递归），默认 settings.knowledge_docs_dir。

    单个文件解析失败只记录日志并跳过，不中断整批导入。
    """
    directory = Path(directory) if directory else settings.knowledge_docs_dir
    documents: list[Document] = []
    for file_path in sorted(directory.rglob("*")):
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_SUFFIXES:
            try:
                documents.extend(load_document(file_path))
            except Exception:  # 单文件失败不应中断整批导入（已记日志，BLE001 豁免）
                logger.warning("解析失败，已跳过：%s", file_path, exc_info=True)
    return documents


def split_documents(
    documents: list[Document],
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Document]:
    """按配置切分文档（默认 settings.chunk_size / chunk_overlap，中文分隔符优先）。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size or settings.chunk_size,
        chunk_overlap=chunk_overlap if chunk_overlap is not None else settings.chunk_overlap,
        separators=SEPARATORS,
        length_function=len,
    )
    return splitter.split_documents(documents)


class RagService:
    """知识库服务：文档入库（解析 → 切分 → 嵌入）与相似度检索。

    向量库持久化到 settings.chroma_dir；每个知识库一个 collection（kb_{id}）。
    Chroma 连接是惰性的：未配置 API Key 时，解析与切分仍可离线使用。
    """

    def __init__(
        self,
        collection_name: str = "knowledge",
        persist_directory: str | Path | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        self.collection_name = collection_name
        self.persist_directory = (
            Path(persist_directory) if persist_directory else settings.chroma_dir
        )
        self.chunk_size = chunk_size or settings.chunk_size
        self.chunk_overlap = (
            chunk_overlap if chunk_overlap is not None else settings.chunk_overlap
        )
        self._store: Chroma | None = None

    @property
    def store(self) -> Chroma:
        """向量库连接（惰性建立：需要已配置 DASHSCOPE_API_KEY）。"""
        if self._store is None:
            self.persist_directory.mkdir(parents=True, exist_ok=True)
            self._store = Chroma(
                collection_name=self.collection_name,
                embedding_function=get_embeddings(),
                persist_directory=str(self.persist_directory),
            )
        return self._store

    def _index(self, documents: list[Document]) -> int:
        """切分并写入向量库，返回写入的片段数量。"""
        if not documents:
            return 0
        chunks = split_documents(documents, self.chunk_size, self.chunk_overlap)
        if not chunks:
            return 0
        self.store.add_documents(chunks)
        logger.info(
            "入库完成：collection=%s，%d 个文档 → %d 个片段",
            self.collection_name,
            len(documents),
            len(chunks),
        )
        return len(chunks)

    def add_documents(self, file_paths: list[str | Path]) -> int:
        """解析并入库指定文件，返回写入的片段（chunk）数量。"""
        documents: list[Document] = []
        for file_path in file_paths:
            documents.extend(load_document(file_path))
        return self._index(documents)

    def add_directory(self, directory: str | Path | None = None) -> int:
        """批量入库目录（递归），默认 settings.knowledge_docs_dir；返回片段数量。"""
        return self._index(load_directory(directory))

    def search(self, query: str, k: int | None = None) -> list[Document]:
        """相似度检索，返回最相关的 k 个片段（默认 settings.top_k）。"""
        return self.store.similarity_search(query, k=k or settings.top_k)

    def list_documents(self) -> list[dict[str, Any]]:
        """按源文件聚合已入库文档：[{name, chunks}]（按文件名排序；空库返回 []）。"""
        metadatas = self.store.get(include=["metadatas"]).get("metadatas") or []
        counts: dict[str, int] = {}
        for meta in metadatas:
            if not meta:
                continue
            name = Path(str(meta.get("source", ""))).name or "未知来源"
            counts[name] = counts.get(name, 0) + 1
        return [{"name": name, "chunks": count} for name, count in sorted(counts.items())]

    def delete_document(self, source: str | Path) -> int:
        """删除某源文件（source 路径精确匹配）的全部片段，返回删除数量。"""
        source_key = str(source)
        data = self.store.get(where={"source": source_key}, include=["metadatas"])
        ids = list(data.get("ids") or [])
        if ids:
            self.store.delete(ids=ids)
        logger.info("已删除文档片段：source=%s，共 %d 个", source_key, len(ids))
        return len(ids)

    def clear(self) -> None:
        """清空当前知识库（删除并重建 collection，避免残留旧向量）。"""
        self.store.reset_collection()
        logger.info("已清空知识库：collection=%s", self.collection_name)


@lru_cache
def get_rag_service() -> RagService:
    """RagService 单例（进程内共用；API 路由与 Agent 工具都经此获取）。"""
    return RagService()
