"""
rag/embedder.py
Embedding 封装：支持本地 sentence-transformers（默认）或 OpenAI API。

选择方式（.env 配置）：
    EMBEDDING_BACKEND=local        # 默认，使用 sentence-transformers（离线）
    EMBEDDING_BACKEND=openai       # 需配置 OPENAI_API_KEY

本地模型：paraphrase-multilingual-MiniLM-L12-v2
  - 支持中文 / 英文 / 多语言混合
  - 输出维度：384
  - 约 120MB，首次运行自动下载
"""
import os
import logging
from typing import List

logger = logging.getLogger(__name__)

# 本地模型名称（Hugging Face 模型 ID）
_LOCAL_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# OpenAI 模型
_OPENAI_MODEL_NAME = "text-embedding-3-small"


class Embedder:
    """
    统一 Embedding 接口。

    Usage:
        embedder = Embedder()                  # 读取 .env 配置自动选择后端
        vectors = embedder.embed(["文本1", "文本2"])
        vec = embedder.embed_one("单条文本")
    """

    def __init__(self, backend: str = None):
        """
        Args:
            backend: "local" 或 "openai"。None 时从环境变量 EMBEDDING_BACKEND 读取，
                     缺省为 "local"。
        """
        self._backend = (backend or os.getenv("EMBEDDING_BACKEND", "local")).lower()
        self._model = None       # 延迟初始化

        if self._backend not in ("local", "openai"):
            logger.warning(f"未知 backend '{self._backend}'，回退到 'local'")
            self._backend = "local"

        logger.info(f"[Embedder] 使用后端: {self._backend}")

    # ── 内部初始化 ────────────────────────────────────────────────────────

    def _ensure_local_model(self):
        """懒加载本地 sentence-transformers 模型。"""
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            logger.info(f"[Embedder] 加载本地模型: {_LOCAL_MODEL_NAME}（首次运行会自动下载）")
            self._model = SentenceTransformer(_LOCAL_MODEL_NAME)
            dim = getattr(self._model, "get_embedding_dimension", None) or getattr(self._model, "get_sentence_embedding_dimension", None)
            logger.info(f"[Embedder] 本地模型加载完成，维度={dim() if dim else '未知'}")
        except ImportError:
            raise ImportError(
                "缺少 sentence-transformers 库，请运行：\n"
                "  pip install sentence-transformers"
            )

    def _ensure_openai_client(self):
        """懒加载 OpenAI 客户端。"""
        if self._model is not None:
            return
        try:
            from openai import OpenAI
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("使用 OpenAI Embedding 需在 .env 中配置 OPENAI_API_KEY")
            self._model = OpenAI(api_key=api_key)
            logger.info(f"[Embedder] OpenAI 客户端初始化完成，模型: {_OPENAI_MODEL_NAME}")
        except ImportError:
            raise ImportError(
                "缺少 openai 库，请运行：\n"
                "  pip install openai>=1.0.0"
            )

    # ── 公开接口 ──────────────────────────────────────────────────────────

    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        批量 Embedding。

        Args:
            texts: 文本列表

        Returns:
            List of float vectors
        """
        if not texts:
            return []

        if self._backend == "local":
            self._ensure_local_model()
            vectors = self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
            return [v.tolist() for v in vectors]

        elif self._backend == "openai":
            self._ensure_openai_client()
            response = self._model.embeddings.create(
                model=_OPENAI_MODEL_NAME,
                input=texts,
            )
            return [item.embedding for item in response.data]

        else:
            raise ValueError(f"不支持的 backend: {self._backend}")

    def embed_one(self, text: str) -> List[float]:
        """单条 Embedding（底层复用 embed()）。"""
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        """返回 embedding 向量维度。"""
        if self._backend == "local":
            self._ensure_local_model()
            dim_fn = getattr(self._model, "get_embedding_dimension", None) or getattr(self._model, "get_sentence_embedding_dimension", None)
            return dim_fn() if dim_fn else 384
        elif self._backend == "openai":
            return 1536  # text-embedding-3-small 维度
        return 384
