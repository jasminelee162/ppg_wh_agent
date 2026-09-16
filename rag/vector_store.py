"""
rag/vector_store.py
向量库封装（ChromaDB）：持久化到 output/rag_index/，支持按 category Metadata 过滤。

Collection 设计：
  - 使用单个 collection "logistics_knowledge"
  - 通过 Metadata 字段 "category" 实现逻辑分区：
      transport_modes / goods_classification / sop / general
  - 支持 upsert（重复 chunk_id 更新而非插入重复）

ChromaDB 版本兼容性说明：
  - 使用 chromadb >= 0.4.0 的现代 API
  - 持久化客户端：chromadb.PersistentClient(path=...)
"""
import os
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# ChromaDB collection 名称（单库多 category）
_COLLECTION_NAME = "logistics_knowledge"


class VectorStore:
    """
    ChromaDB 向量库封装。

    Usage:
        store = VectorStore(index_dir="output/rag_index")
        store.upsert(chunks, embeddings)
        results = store.query(query_vector, top_k=3, category="transport_modes")
    """

    def __init__(self, index_dir: str):
        """
        Args:
            index_dir: 向量索引持久化目录（如 output/rag_index/）
        """
        self._index_dir = index_dir
        self._client = None
        self._collection = None

    def _ensure_client(self):
        """懒加载 ChromaDB 持久化客户端。"""
        if self._client is not None:
            return
        try:
            import chromadb
        except ImportError:
            raise ImportError(
                "缺少 chromadb 库，请运行：\n"
                "  pip install chromadb>=0.4.0"
            )

        os.makedirs(self._index_dir, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self._index_dir)
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},   # 使用余弦距离
        )
        count = self._collection.count()
        logger.info(f"[VectorStore] ChromaDB 初始化完成，当前索引: {count} 条，路径: {self._index_dir}")

    # ── 写入 ──────────────────────────────────────────────────────────────

    def upsert(
        self,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
    ):
        """
        将 Chunk 和对应 Embedding 写入向量库（upsert 语义，重复 id 更新）。

        Args:
            chunks:     来自 document_loader 的 chunk 列表
                        每项包含 chunk_id / text / metadata
            embeddings: 与 chunks 一一对应的 float 向量列表
        """
        self._ensure_client()

        if not chunks:
            logger.warning("[VectorStore] upsert 收到空 chunks，跳过")
            return

        ids        = [c["chunk_id"]            for c in chunks]
        documents  = [c["text"]                for c in chunks]
        metadatas  = [c["metadata"]            for c in chunks]

        # ChromaDB metadata 值必须是 str / int / float / bool，不能嵌套 dict
        # document_loader 已确保 metadata 值均为 str，此处做一次防御性转换
        metadatas = [
            {k: str(v) for k, v in m.items()}
            for m in metadatas
        ]

        # 分批写入（ChromaDB 单次建议 < 1000 条）
        batch_size = 500
        for i in range(0, len(ids), batch_size):
            batch_slice = slice(i, i + batch_size)
            self._collection.upsert(
                ids        = ids[batch_slice],
                documents  = documents[batch_slice],
                metadatas  = metadatas[batch_slice],
                embeddings = embeddings[batch_slice],
            )
            logger.debug(f"[VectorStore] upsert batch {i // batch_size + 1}：{len(ids[batch_slice])} 条")

        logger.info(f"[VectorStore] upsert 完成，共 {len(ids)} 条，当前总量: {self._collection.count()}")

    def clear(self):
        """清空当前 collection（全量重建时使用）。"""
        self._ensure_client()
        # 删除后重新创建
        self._client.delete_collection(_COLLECTION_NAME)
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("[VectorStore] collection 已清空")

    # ── 查询 ──────────────────────────────────────────────────────────────

    def query(
        self,
        query_embedding: List[float],
        top_k: int = 3,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        向量相似度查询，支持 Metadata 过滤。

        Args:
            query_embedding: 查询文本的 embedding 向量
            top_k:           返回最相似的 K 条结果
            category:        若指定，只在该 category 内检索（Metadata 过滤）

        Returns:
            List of {
                "text":     str,    # Chunk 原文
                "metadata": dict,   # {"source_file", "category", "heading"}
                "score":    float,  # 相似度分数（余弦，越高越相关）
            }
        """
        self._ensure_client()

        where = {"category": category} if category else None

        # ChromaDB query 参数
        query_kwargs: Dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results":         min(top_k, max(1, self._collection.count())),
            "include":          ["documents", "metadatas", "distances"],
        }
        if where:
            query_kwargs["where"] = where

        try:
            result = self._collection.query(**query_kwargs)
        except Exception as e:
            logger.error(f"[VectorStore] query 失败: {e}")
            return []

        hits = []
        for doc, meta, dist in zip(
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            # ChromaDB 余弦距离 = 1 - 余弦相似度，转换为相似度分数
            score = round(1.0 - float(dist), 4)
            hits.append({
                "text":     doc,
                "metadata": meta,
                "score":    score,
            })

        return hits

    @property
    def count(self) -> int:
        """返回当前索引条目数。"""
        self._ensure_client()
        return self._collection.count()
