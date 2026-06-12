"""
Embedding 函数封装 — 支持 OpenAI / 本地 BGE 中文模型 / ChromaDB 默认。

Usage:
    from app.rag.embedding import get_embedding_function
    ef = get_embedding_function()
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from chromadb.api.types import Documents, EmbeddingFunction

from app.config import get_config

logger = logging.getLogger(__name__)


class OpenAICompatibleEmbedding(EmbeddingFunction):
    """OpenAI-compatible embedding (text-embedding-3-small 等)。"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None) -> None:
        cfg = get_config()
        self.api_key = api_key or cfg.embedding_api_key
        self.base_url = base_url or cfg.embedding_base_url or cfg.llm_base_url
        self.model = model or cfg.embedding_model
        self._dim = cfg.embedding_dim

    def __call__(self, input: Documents) -> Sequence[Sequence[float]]:
        import asyncio
        if isinstance(input, str):
            input = [input]
        valid = [t for t in input if t and t.strip()]
        if not valid:
            return [[0.0] * self._dim] * len(input)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as e:
                    return e.submit(asyncio.run, self._async_embed(valid)).result(timeout=30)
            embeddings = asyncio.run(self._async_embed(valid))
        except RuntimeError:
            embeddings = asyncio.run(self._async_embed(valid))
        result, idx = [], 0
        for t in input:
            if t and t.strip():
                result.append(embeddings[idx]); idx += 1
            else:
                result.append([0.0] * self._dim)
        return result

    async def _async_embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        url = f"{self.base_url.rstrip('/')}/embeddings"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }, json={"model": self.model, "input": texts})
            resp.raise_for_status()
            data = resp.json()
        return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]


class BGEEmbedding(EmbeddingFunction):
    """
    本地 BGE 中文 embedding — BAAI/bge-large-zh (1024维) 或 bge-base-zh (768维)。

    首次加载会下载模型（~1.3GB for large, ~400MB for base），之后缓存在本地。
    """

    def __init__(self, model_name: str = "BAAI/bge-large-zh") -> None:
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading BGE model: {self.model_name} ...")
            self._model = SentenceTransformer(self.model_name)
            logger.info(f"BGE model loaded, dim={self._model.get_sentence_embedding_dimension()}")
        return self._model

    def __call__(self, input: Documents) -> Sequence[Sequence[float]]:
        if isinstance(input, str):
            input = [input]
        texts = [t for t in input if t and t.strip()]
        if not texts:
            dim = self.model.get_sentence_embedding_dimension()
            return [[0.0] * dim] * len(input)
        embeddings = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        result, idx = [], 0
        dim = embeddings.shape[1]
        for t in input:
            if t and t.strip():
                result.append(embeddings[idx].tolist()); idx += 1
            else:
                result.append([0.0] * dim)
        return result


class PassthroughEmbedding(EmbeddingFunction):
    """降级零向量 embedding。"""
    def __init__(self, dim: int = 1536) -> None:
        self._dim = dim
    def __call__(self, input: Documents) -> Sequence[Sequence[float]]:
        n = 1 if isinstance(input, str) else len(input)
        return [[0.0] * self._dim] * n


def get_embedding_function() -> EmbeddingFunction:
    """
    获取 embedding 函数，按 EMBEDDING_PROVIDER 环境变量选择：

    - openai:      text-embedding-3-small (需 EMBEDDING_API_KEY)
    - local_bge:   BAAI/bge-large-zh (本地中文优化，1024维)
    - chroma_default: ChromaDB 内置 all-MiniLM-L6-v2 (英文，384维，降级)
    """
    cfg = get_config()
    provider = cfg.embedding_provider

    # 1. OpenAI
    if provider == "openai" and cfg.embedding_api_key and cfg.embedding_api_key not in (
        "your_openai_api_key_here", "", "sk-your-embedding-key",
    ):
        logger.info(f"Embedding: OpenAI ({cfg.embedding_model})")
        return OpenAICompatibleEmbedding(api_key=cfg.embedding_api_key,
                                         base_url=cfg.embedding_base_url or cfg.llm_base_url,
                                         model=cfg.embedding_model)

    # 2. 本地 BGE 中文
    if provider == "local_bge":
        try:
            import sentence_transformers  # noqa
            logger.info("Embedding: BAAI/bge-large-zh (local)")
            return BGEEmbedding(model_name="BAAI/bge-large-zh")
        except ImportError:
            logger.warning("sentence-transformers not installed, falling back to ChromaDB default")

    # 3. ChromaDB 默认
    try:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        logger.info("Embedding: ChromaDB DefaultEmbeddingFunction (all-MiniLM-L6-v2)")
        return DefaultEmbeddingFunction()
    except ImportError:
        logger.warning("No embedding available, using Passthrough")
        return PassthroughEmbedding(dim=cfg.embedding_dim)


# ── 语义相似度工具（模块 4 用） ──

_similarity_ef: EmbeddingFunction | None = None

def semantic_similarity(text_a: str, text_b: str) -> float:
    """
    计算两个文本的余弦相似度（0~1）。

    用于增量实体语义比较（地址/物品名）。
    """
    import numpy as np
    global _similarity_ef
    if _similarity_ef is None:
        _similarity_ef = get_embedding_function()

    emb_a = _similarity_ef([text_a])[0]
    emb_b = _similarity_ef([text_b])[0]
    a, b = np.array(emb_a), np.array(emb_b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))
