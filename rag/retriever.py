"""
rag/retriever.py
检索器：将查询文本 embed 后，从向量库中检索相关 Chunk，支持 Metadata 过滤。

这一层是 rag_pipeline 与 vector_store 之间的适配层，负责：
1. 将原始文本查询转换为 embedding
2. 调用 VectorStore.query() 获取结果
3. 对结果做后处理（分数阈值过滤、格式标准化）
"""
import logging
from typing import List, Dict, Any, Optional

from .embedder import Embedder
from .vector_store import VectorStore

logger = logging.getLogger(__name__)

# 默认相似度阈值：低于此分数的结果被过滤（余弦相似度，范围 0~1）
_DEFAULT_SCORE_THRESHOLD = 0.30


class Retriever:
    """
    语义检索器。

    Usage:
        retriever = Retriever(embedder, vector_store)
        hits = retriever.query(
            "零担运输危险品",
            category="transport_modes",
            top_k=3,
            score_threshold=0.35,
        )
    """

    def __init__(self, embedder: Embedder, vector_store: VectorStore):
        self._embedder = embedder
        self._store    = vector_store

    def query(
        self,
        text: str,
        category: Optional[str] = None,
        top_k: int = 3,
        score_threshold: float = _DEFAULT_SCORE_THRESHOLD,
    ) -> List[Dict[str, Any]]:
        """
        检索最相关的 Chunk。

        Args:
            text:            查询文本（自然语言）
            category:        Metadata 过滤（transport_modes / goods_classification / sop / None=全库）
            top_k:           返回条数上限
            score_threshold: 最低相似度阈值，低于此值的结果被丢弃

        Returns:
            List of {
                "text":        str,   # Chunk 原文（含标题）
                "metadata":    dict,  # source_file / category / heading
                "score":       float, # 余弦相似度（0~1）
            }
        """
        if not text or not text.strip():
            return []

        try:
            query_vec = self._embedder.embed_one(text.strip())
        except Exception as e:
            logger.error(f"[Retriever] embedding 失败: {e}")
            return []

        raw_hits = self._store.query(
            query_embedding=query_vec,
            top_k=top_k,
            category=category,
        )

        # 应用分数阈值过滤
        filtered = [h for h in raw_hits if h["score"] >= score_threshold]

        if filtered:
            logger.debug(
                f"[Retriever] query='{text[:40]}' category={category} "
                f"→ {len(filtered)}/{len(raw_hits)} hits (threshold={score_threshold})"
            )
        else:
            logger.debug(
                f"[Retriever] query='{text[:40]}' → 无有效结果（最高分: "
                f"{raw_hits[0]['score'] if raw_hits else 'N/A'}）"
            )

        return filtered

    def query_multi_category(
        self,
        text: str,
        categories: List[str],
        top_k_each: int = 2,
        score_threshold: float = _DEFAULT_SCORE_THRESHOLD,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        同时在多个 category 中检索，返回按 category 分组的结果。

        Args:
            text:        查询文本
            categories:  category 列表
            top_k_each:  每个 category 返回条数
            score_threshold: 最低分数

        Returns:
            { category: [hit, ...] }
        """
        results: Dict[str, List[Dict[str, Any]]] = {}
        for cat in categories:
            hits = self.query(text, category=cat, top_k=top_k_each, score_threshold=score_threshold)
            results[cat] = hits
        return results
