"""
rag/rag_pipeline.py
RAG 总入口：封装 build（构建索引）、query（单次检索）、explain（订单综合解释）。

设计原则：
- 单例懒加载：第一次 query 时才初始化 Embedder / VectorStore / Retriever
- 轻量调用：业务代码只需 `from rag import RAGPipeline; rag = RAGPipeline()`
- 安全兜底：任何异常均被捕获并记录，不影响主流程
"""
import os
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# 默认路径（相对于项目根目录）
_DEFAULT_KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge")
_DEFAULT_INDEX_DIR     = os.path.join(os.path.dirname(__file__), "..", "output", "rag_index")


class RAGPipeline:
    """
    物流领域 RAG 知识库管道。

    Usage：
        rag = RAGPipeline()

        # 构建 / 重建索引（知识文档更新后执行）
        rag.build(force_rebuild=True)

        # 单次语义检索
        hits = rag.query("零担运输危险品", category="transport_modes", top_k=3)

        # 对一条订单进行多维度推断（兜底提示，不覆盖核心字段）
        hints = rag.explain(order_dict)
    """

    def __init__(
        self,
        knowledge_dir: str = None,
        index_dir: str = None,
        embedding_backend: str = None,
    ):
        """
        Args:
            knowledge_dir:      知识文档目录（默认 knowledge/）
            index_dir:          向量索引持久化目录（默认 output/rag_index/）
            embedding_backend:  "local" 或 "openai"（默认读取 .env EMBEDDING_BACKEND，缺省 local）
        """
        self._knowledge_dir     = os.path.abspath(knowledge_dir or _DEFAULT_KNOWLEDGE_DIR)
        self._index_dir         = os.path.abspath(index_dir     or _DEFAULT_INDEX_DIR)
        self._embedding_backend = embedding_backend

        # 延迟初始化
        self._embedder    = None
        self._store       = None
        self._retriever   = None
        self._initialized = False

    # ── 内部初始化 ────────────────────────────────────────────────────────

    def _init_components(self):
        """懒加载所有 RAG 组件（仅首次调用时执行）。"""
        if self._initialized:
            return
        try:
            from .embedder    import Embedder
            from .vector_store import VectorStore
            from .retriever   import Retriever

            self._embedder  = Embedder(backend=self._embedding_backend)
            self._store     = VectorStore(index_dir=self._index_dir)
            self._retriever = Retriever(self._embedder, self._store)
            self._initialized = True
            logger.info("[RAGPipeline] 组件初始化完成")
        except Exception as e:
            logger.error(f"[RAGPipeline] 初始化失败: {e}", exc_info=True)
            raise

    # ── 构建索引 ──────────────────────────────────────────────────────────

    def build(self, force_rebuild: bool = False):
        """
        构建（或增量更新）向量索引。

        Args:
            force_rebuild: True 时先清空索引再重建；False 时 upsert 增量更新。
        """
        self._init_components()

        from .document_loader import load_knowledge_documents

        logger.info(f"[RAGPipeline] 开始{'全量重建' if force_rebuild else '增量更新'}索引...")
        logger.info(f"  知识目录: {self._knowledge_dir}")
        logger.info(f"  索引目录: {self._index_dir}")

        chunks = load_knowledge_documents(self._knowledge_dir)
        if not chunks:
            logger.warning("[RAGPipeline] 知识文档为空，索引构建跳过")
            return

        if force_rebuild:
            self._store.clear()

        # 批量 Embedding
        texts      = [c["text"] for c in chunks]
        logger.info(f"[RAGPipeline] 计算 Embedding：{len(texts)} 个 Chunk...")
        embeddings = self._embedder.embed(texts)

        # 写入向量库
        self._store.upsert(chunks, embeddings)
        logger.info(f"[RAGPipeline] 索引构建完成，共 {self._store.count} 条")

    # ── 检索 ──────────────────────────────────────────────────────────────

    def query(
        self,
        text: str,
        category: Optional[str] = None,
        top_k: int = 3,
        score_threshold: float = 0.30,
    ) -> List[Dict[str, Any]]:
        """
        语义检索。

        Args:
            text:            查询文本
            category:        Metadata 过滤（transport_modes / goods_classification / sop / None=全库）
            top_k:           最多返回条数
            score_threshold: 最低相似度（0~1）

        Returns:
            List of {"text": str, "metadata": dict, "score": float}
            若索引为空或查询失败，返回空列表（不抛异常）。
        """
        try:
            self._init_components()
            if self._store.count == 0:
                logger.warning("[RAGPipeline] 向量索引为空，请先运行 rag/build_index.py")
                return []
            return self._retriever.query(text, category=category, top_k=top_k, score_threshold=score_threshold)
        except Exception as e:
            logger.error(f"[RAGPipeline] query 异常: {e}", exc_info=True)
            return []

    # ── 订单综合解释 ──────────────────────────────────────────────────────

    def explain(self, order_dict: dict) -> Dict[str, Any]:
        """
        对一条订单 dict 进行多维度 RAG 推断，返回各维度的参考提示。

        结果字段（均以 _rag_hint 后缀命名，不直接覆盖核心字段）：
            shipping_rag_hint:    发运方式推断参考
            danger_rag_hint:      危险品类别判断参考
            sop_rag_hint:         SOP 操作提醒

        Args:
            order_dict: 已经过 FieldNormalizer 处理的订单 dict

        Returns:
            dict，键为 hint 字段名，值为最相关 Chunk 的文本（空列表时为 ""）
        """
        hints: Dict[str, Any] = {
            "shipping_rag_hint": "",
            "danger_rag_hint":   "",
            "sop_rag_hint":      "",
        }

        # ── 构造查询文本 ──────────────────────────────────────────────────
        address     = order_dict.get("address", "")
        requirement = order_dict.get("requirement", "")
        company     = order_dict.get("company_name", "")
        weight_raw  = order_dict.get("weight", "")

        # 1. 发运方式推断（基于地址 + 客户要求 + 重量）
        shipping_query = " ".join(filter(None, [
            requirement,
            address,
            str(weight_raw),
            order_dict.get("发运方式", ""),
        ]))
        if shipping_query.strip():
            hits = self.query(shipping_query, category="transport_modes", top_k=2)
            if hits:
                hints["shipping_rag_hint"] = hits[0]["text"]

        # 2. 危险品判断（基于公司名 + 客户要求）
        danger_query = " ".join(filter(None, [
            company,
            requirement,
            order_dict.get("危险品类别", ""),
        ]))
        if danger_query.strip():
            hits = self.query(danger_query, category="goods_classification", top_k=2)
            if hits:
                hints["danger_rag_hint"] = hits[0]["text"]

        # 3. SOP 提醒（基于综合信息）
        sop_query = " ".join(filter(None, [
            requirement,
            address,
            order_dict.get("发运方式", ""),
            order_dict.get("危险品类别", ""),
        ]))
        if sop_query.strip():
            hits = self.query(sop_query, category="sop", top_k=1)
            if hits:
                hints["sop_rag_hint"] = hits[0]["text"]

        return hints
