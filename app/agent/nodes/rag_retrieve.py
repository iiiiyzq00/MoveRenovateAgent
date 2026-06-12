"""
RAG 检索节点 — node_rag_retrieve。

基于风险 6 优化：直接消费 intent_classify 产出的 _rag_prejudge，
无需额外 LLM 调用做 query_type 分类。

Usage:
    from app.agent.nodes.rag_retrieve import RAGRetrieveNode
    node = RAGRetrieveNode()
    result = await node(state)
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent.state import MasterState

logger = logging.getLogger(__name__)


class RAGRetrieveNode:
    """
    RAG 检索节点 — 自适应知识库检索。

    输入: MasterState（读取 _rag_prejudge, last_user_input）
    输出: dict（更新 rag_context）

    检索流程:
    1. 从 _rag_prejudge 获取 query_type / rag_queries / rag_priority
    2. 若 rag_priority == "none" → 跳过检索
    3. 调用 AdaptiveRetriever 执行检索（单条或批量）
    4. 写出 rag_context
    """

    def __init__(self) -> None:
        self._retriever = None  # 懒加载

    @property
    def retriever(self):
        """懒加载 AdaptiveRetriever。"""
        if self._retriever is None:
            from app.rag.retriever import AdaptiveRetriever
            self._retriever = AdaptiveRetriever()
        return self._retriever

    async def __call__(self, state: MasterState) -> dict:
        """
        执行 RAG 检索。

        使用意图分类产出的 _rag_prejudge（风险 6 优化），无需再次调用 LLM。
        """
        # 1. 获取 RAG 预判
        rag_pre = state.get("_rag_prejudge", {}) or {}
        rag_priority = rag_pre.get("rag_priority", "optional")
        query_type = rag_pre.get("query_type")
        rag_queries = rag_pre.get("rag_queries", [])

        # 2. 若无需检索，直接返回
        if rag_priority == "none" or (not rag_queries and not query_type):
            logger.info("[rag] No RAG retrieval needed (priority=none or no queries)")
            return {"rag_context": None}

        # 3. 若 rag_queries 为空，以用户原始输入作为 query
        if not rag_queries:
            rag_queries = [state.get("last_user_input", "")]
            query_type = query_type or "general_faq"

        # 4. 执行检索
        logger.info(f"[rag] Retrieving: type={query_type}, queries={rag_queries}, priority={rag_priority}")

        if len(rag_queries) == 1:
            result = await self.retriever.retrieve(
                query=rag_queries[0],
                query_type=query_type,
            )
        else:
            result = await self.retriever.retrieve_multi(
                queries=rag_queries,
                query_type=query_type,
            )

        # 5. 构建输出
        docs = result.get("docs", [])
        warning = result.get("warning")
        total = result.get("total_candidates", 0)

        logger.info(
            f"[rag] Retrieved {total} docs (type={query_type}, "
            f"threshold={result.get('threshold_used')}), warning={warning}"
        )

        rag_context: dict[str, Any] = {
            "query_type": query_type,
            "retrieved_docs": docs,
            "threshold_used": result.get("threshold_used", 0.0),
            "warning": warning,
            "rag_priority": rag_priority,
        }

        return {"rag_context": rag_context}

    async def retrieve_for_agent(
        self,
        query: str,
        query_type: str | None = None,
    ) -> dict[str, Any]:
        """
        供 Agent 节点主动调用的检索方法（Agent 在推理过程中发现需要查知识库）。

        Args:
            query: 自然语言查询
            query_type: 可选的 query_type 覆盖

        Returns:
            RAG 检索结果 dict
        """
        qtype = query_type or "general_faq"
        return await self.retriever.retrieve(query=query, query_type=qtype)

    def format_for_prompt(self, rag_context: dict[str, Any] | None) -> str:
        """
        将 rag_context 格式化为可注入 Agent Prompt 的文本片段。

        Args:
            rag_context: RAG 检索结果

        Returns:
            格式化的文本（可直接插入 System Prompt）
        """
        if not rag_context or not rag_context.get("retrieved_docs"):
            return "（知识库中暂无相关信息）"

        docs = rag_context["retrieved_docs"]
        warning = rag_context.get("warning")

        lines = ["## 📚 知识库参考信息\n"]

        if warning == "low_confidence":
            lines.append("> ⚠️ 以下信息与您的问题相关性较低，仅供参考\n")
        elif warning == "no_knowledge":
            lines.append("> ⚠️ 知识库中暂无与此问题直接相关的信息\n")
            return "\n".join(lines)

        for i, doc in enumerate(docs[:8], 1):  # 最多 8 条，控制 token
            authority = doc.get("authority_level", "unknown")
            authority_label = {
                "mandatory_standard": "📗 国家标准",
                "industry_best_practice": "📘 行业实践",
                "llm_generated": "📙 参考知识",
                "user_contributed": "📓 用户贡献",
            }.get(authority, "📓 其他")

            lines.append(
                f"**{i}. {authority_label}** (相似度: {doc['score']:.2f})\n"
                f"{doc['content'][:300]}\n"  # 截断长文本
            )

        return "\n".join(lines)
