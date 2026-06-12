"""
自适应检索器 — 7 类 query_type × 动态阈值 & topK。

基于第二阶段 5.2 设计，支持三级放宽策略：
1. 初始参数检索
2. topK 放宽
3. 阈值放宽
4. 无结果告知

Usage:
    from app.rag.retriever import AdaptiveRetriever
    retriever = AdaptiveRetriever()
    result = await retriever.retrieve(query="冰箱怎么打包", query_type="item_packing")
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_config

logger = logging.getLogger(__name__)

# ── 7 类查询配置 ──────────────────────────────────────────
# ── 阈值：按 embedding provider 自动调整 ──

def _get_query_config() -> dict:
    """返回当前 embedding provider 适配的阈值配置。"""
    from app.config import get_config
    provider = get_config().embedding_provider

    if provider == "openai":
        # OpenAI text-embedding-3-small: 高精度，使用严格阈值
        return {
            "item_packing":          {"threshold": 0.65, "topK": 5,  "max_topK": 10},
            "construction_standard": {"threshold": 0.55, "topK": 8,  "max_topK": 15},
            "safety_code":           {"threshold": 0.70, "topK": 5,  "max_topK": 10},
            "space_dimension":       {"threshold": 0.60, "topK": 5,  "max_topK": 10},
            "quantity_estimation":   {"threshold": 0.50, "topK": 10, "max_topK": 20},
            "cost_reference":        {"threshold": 0.50, "topK": 10, "max_topK": 15},
            "general_faq":           {"threshold": 0.55, "topK": 5,  "max_topK": 10},
        }
    elif provider == "local_bge":
        # BGE-large-zh: 中文优化，中等阈值
        return {
            "item_packing":          {"threshold": 0.55, "topK": 5,  "max_topK": 10},
            "construction_standard": {"threshold": 0.50, "topK": 8,  "max_topK": 15},
            "safety_code":           {"threshold": 0.60, "topK": 5,  "max_topK": 10},
            "space_dimension":       {"threshold": 0.52, "topK": 5,  "max_topK": 10},
            "quantity_estimation":   {"threshold": 0.48, "topK": 10, "max_topK": 20},
            "cost_reference":        {"threshold": 0.45, "topK": 10, "max_topK": 15},
            "general_faq":           {"threshold": 0.50, "topK": 5,  "max_topK": 10},
        }
    else:
        # ChromaDB 默认 all-MiniLM-L6-v2: 英文模型，中文精度低，降阈值
        return {
            "item_packing":          {"threshold": 0.45, "topK": 5,  "max_topK": 10},
            "construction_standard": {"threshold": 0.40, "topK": 8,  "max_topK": 15},
            "safety_code":           {"threshold": 0.50, "topK": 5,  "max_topK": 10},
            "space_dimension":       {"threshold": 0.42, "topK": 5,  "max_topK": 10},
            "quantity_estimation":   {"threshold": 0.38, "topK": 10, "max_topK": 20},
            "cost_reference":        {"threshold": 0.35, "topK": 10, "max_topK": 15},
            "general_faq":           {"threshold": 0.40, "topK": 5,  "max_topK": 10},
        }


QUERY_CONFIG = _get_query_config()

DEFAULT_CONFIG: dict[str, Any] = {
    "threshold": 0.45,
    "topK": 5,
    "max_topK": 10,
}

# authority_level 降权系数
AUTHORITY_PENALTY: dict[str, float] = {
    "mandatory_standard": 0.0,       # 不降权
    "industry_best_practice": -0.05, # 略降
    "llm_generated": -0.10,          # 明显降权
    "user_contributed": -0.10,       # 同 LLM 生成
}


class AdaptiveRetriever:
    """
    自适应检索器。

    核心功能：
    - 按 query_type 选择初始 threshold 和 topK
    - 无足够结果时自动放宽（topK → threshold）
    - authority_level 加权（规范 > 实践 > 生成）
    - 返回结构化 RAGResult（供 Agent 和溯源使用）
    """

    def __init__(self, collection: Any | None = None, embedding_fn: Any = None) -> None:
        """
        Args:
            collection: ChromaDB collection 实例。None 则自动获取。
            embedding_fn: Embedding 函数（HTTP 模式必须提供，否则无法 embed 查询文本）
        """
        self._collection = collection
        self._embedding_fn = embedding_fn

    @property
    def collection(self):
        """懒加载 collection。"""
        if self._collection is None:
            from app.rag.chroma_client import KnowledgeBaseManager
            manager = KnowledgeBaseManager(embedding_fn=self._embedding_fn)
            self._collection = manager.get_or_create_collection()
        return self._collection

    @property
    def embedding_fn(self):
        if self._embedding_fn is None:
            from app.rag.embedding import get_embedding_function
            self._embedding_fn = get_embedding_function()
        return self._embedding_fn

    async def retrieve(
        self,
        query: str,
        query_type: str | None = None,
        top_k_override: int | None = None,
        threshold_override: float | None = None,
    ) -> dict[str, Any]:
        """
        自适应检索。

        Args:
            query: 检索查询文本
            query_type: 7 类之一，None 则使用 default 配置
            top_k_override: 覆盖 topK（用于手动调整）
            threshold_override: 覆盖阈值（用于手动调整）

        Returns:
            RAGResult dict:
                {
                    "query_type": str,
                    "docs": [{"doc_id", "content", "score", "metadata", "authority_level"}],
                    "threshold_used": float,
                    "topK_used": int,
                    "warning": str | None,   # "low_confidence" | "no_knowledge" | None
                    "total_candidates": int,
                }
        """
        cfg = QUERY_CONFIG.get(query_type or "", DEFAULT_CONFIG)
        threshold = threshold_override if threshold_override is not None else cfg["threshold"]
        top_k = top_k_override if top_k_override is not None else cfg["topK"]
        max_top_k = cfg["max_topK"]

        collection = self.collection

        # 如果 collection 为空，直接返回
        if collection.count() == 0:
            return {
                "query_type": query_type or "unknown",
                "docs": [],
                "threshold_used": threshold,
                "topK_used": top_k,
                "warning": "no_knowledge",
                "total_candidates": 0,
            }

        # ── Round 1: 初始检索 ──
        try:
            # 客户端预计算 embedding（避免依赖 ChromaDB 服务端 embedding 函数）
            ef = self.embedding_fn
            query_emb = ef([query])  # 返回 [[float, ...]]
            results = collection.query(
                query_embeddings=query_emb,
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.warning(f"RAG query failed (embedding mismatch?): {e}")
            return {
                "query_type": query_type or "unknown",
                "docs": [],
                "threshold_used": threshold,
                "topK_used": top_k,
                "warning": "no_knowledge",
                "total_candidates": 0,
            }

        docs = self._format_results(results, threshold)

        # ── Round 2: 放宽 topK ──
        if len(docs) < 3 and top_k < max_top_k:
            logger.info(f"Retrieval: only {len(docs)} docs above threshold, expanding topK {top_k}→{max_top_k}")
            results = collection.query(
                query_embeddings=query_emb,
                n_results=max_top_k,
                include=["documents", "metadatas", "distances"],
            )
            docs = self._format_results(results, threshold)
            top_k = max_top_k

        # ── Round 3: 放宽阈值 ──
        warning = None
        if len(docs) < 1:
            relaxed_threshold = max(threshold - 0.15, 0.40)
            logger.info(f"Retrieval: no docs at threshold={threshold}, relaxing to {relaxed_threshold}")
            docs = self._format_results(results, relaxed_threshold)

            if len(docs) > 0:
                warning = "low_confidence"
                threshold = relaxed_threshold
            else:
                warning = "no_knowledge"

        # ── authority_level 加权 ──
        docs = self._apply_authority_weight(docs)

        return {
            "query_type": query_type or "unknown",
            "docs": docs,
            "threshold_used": round(threshold, 2),
            "topK_used": top_k,
            "warning": warning,
            "total_candidates": len(docs),
        }

    def _format_results(
        self,
        results: dict,
        threshold: float,
    ) -> list[dict[str, Any]]:
        """
        将 ChromaDB 返回结果格式化为标准 doc 列表。

        ChromaDB 1.x 返回格式:
        {
            "ids": [[...]],
            "documents": [[...]],
            "metadatas": [[...]],
            "distances": [[...]],
        }
        """
        docs: list[dict[str, Any]] = []

        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for i in range(len(ids)):
            distance = distances[i] if i < len(distances) else 1.0
            # ChromaDB distance → similarity score
            # cosine distance 范围 [0, 2], 归一化到 [0, 1]: score = 1 - distance/2
            # IP (inner product) distance: 越大越相似, 直接用 1/(1+distance)
            raw_score = 1.0 - float(distance) / 2.0
            score = round(max(0.0, min(1.0, raw_score)), 4)

            if score < threshold:
                continue

            meta = metadatas[i] if i < len(metadatas) and metadatas[i] else {}
            docs.append({
                "doc_id": ids[i],
                "content": documents[i] if i < len(documents) else "",
                "score": score,
                "metadata": meta,
                "authority_level": meta.get("authority_level", "llm_generated"),
                "source": meta.get("source", "unknown"),
                "category": meta.get("category", ""),
                "tags": meta.get("tags", []),
            })

        # 按 score 降序
        docs.sort(key=lambda d: d["score"], reverse=True)
        return docs

    def _apply_authority_weight(
        self,
        docs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        对 authority_level 应用降权系数。

        如安全规范查询中，llm_generated 来源的文档应排在 mandatory_standard 之后。
        """
        for doc in docs:
            level = doc.get("authority_level", "llm_generated")
            penalty = AUTHORITY_PENALTY.get(level, -0.05)
            doc["score"] = round(max(0.0, doc["score"] + penalty), 4)
            doc["authority_penalty"] = penalty

        # 重新排序
        docs.sort(key=lambda d: d["score"], reverse=True)
        return docs

    async def retrieve_multi(
        self,
        queries: list[str],
        query_type: str | None = None,
    ) -> dict[str, Any]:
        """
        批量检索（用于 rag_prejudge 产出多条 query 的场景）。

        对每条 query 独立检索，然后合并去重。
        """
        all_docs: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for q in queries:
            result = await self.retrieve(q, query_type=query_type)
            for doc in result["docs"]:
                if doc["doc_id"] not in seen_ids:
                    seen_ids.add(doc["doc_id"])
                    all_docs.append(doc)

        # 合并后按分数排序
        all_docs.sort(key=lambda d: d["score"], reverse=True)

        # 取 top 15
        final_docs = all_docs[:15]

        # 确定 warning 级别（取最严重的）
        warnings = []
        for q in queries:
            r = await self.retrieve(q, query_type=query_type)
            if r.get("warning"):
                warnings.append(r["warning"])

        final_warning = None
        if "no_knowledge" in warnings:
            final_warning = "no_knowledge"
        elif "low_confidence" in warnings:
            final_warning = "low_confidence"

        return {
            "query_type": query_type or "unknown",
            "docs": final_docs,
            "threshold_used": QUERY_CONFIG.get(query_type or "", DEFAULT_CONFIG)["threshold"],
            "topK_used": max(QUERY_CONFIG.get(query_type or "", DEFAULT_CONFIG)["topK"], 10),
            "warning": final_warning,
            "total_candidates": len(final_docs),
        }
