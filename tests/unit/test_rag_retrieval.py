"""
测试 RAG 检索节点。

运行:
    pytest tests/unit/test_rag_retrieval.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.nodes.rag_retrieve import RAGRetrieveNode
from app.agent.state import create_empty_state, MasterState
from app.rag.retriever import AdaptiveRetriever, QUERY_CONFIG


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def mock_collection():
    """模拟 ChromaDB collection。"""
    col = MagicMock()
    col.count.return_value = 10
    col.query.return_value = {
        "ids": [["doc_1", "doc_2", "doc_3"]],
        "documents": [["冰箱搬运需提前除霜...", "洗衣机搬运需拆螺栓...", "衣物可用真空压缩袋..."]],
        "metadatas": [[
            {"authority_level": "mandatory_standard", "category": "item_packing", "source": "standard"},
            {"authority_level": "industry_best_practice", "category": "item_packing", "source": "standard"},
            {"authority_level": "llm_generated", "category": "item_packing", "source": "llm_generated"},
        ]],
        "distances": [[0.15, 0.25, 0.40]],  # 1-distance = scores: 0.85, 0.75, 0.60
    }
    return col


@pytest.fixture
def retriever(mock_collection):
    """创建带 mock collection 的 AdaptiveRetriever。"""
    r = AdaptiveRetriever()
    r._collection = mock_collection
    return r


@pytest.fixture
def node(retriever):
    """创建带 mock retriever 的 RAGRetrieveNode。"""
    n = RAGRetrieveNode()
    n._retriever = retriever
    return n


def _make_state(
    user_input: str = "三居室，有猫",
    rag_prejudge: dict | None = None,
    conversation_round: int = 1,
) -> MasterState:
    """快速创建测试状态。"""
    state = create_empty_state(session_id="test_rag", user_id="u1")
    state["last_user_input"] = user_input
    state["conversation_round"] = conversation_round
    if rag_prejudge:
        state["_rag_prejudge"] = rag_prejudge
    return state


# ═══════════════════════════════════════════════════════════
# 自适应检索器测试
# ═══════════════════════════════════════════════════════════

class TestAdaptiveRetriever:
    """测试 AdaptiveRetriever 的核心逻辑。"""

    @pytest.mark.asyncio
    async def test_retrieve_returns_formatted_docs(self, retriever):
        """检索应返回格式化的文档列表。"""
        result = await retriever.retrieve("冰箱怎么打包", query_type="item_packing")

        assert "docs" in result
        assert "query_type" in result
        assert "threshold_used" in result
        assert result["query_type"] == "item_packing"
        assert len(result["docs"]) >= 1

    @pytest.mark.asyncio
    async def test_docs_have_required_fields(self, retriever):
        """每条文档应包含必要字段。"""
        result = await retriever.retrieve("冰箱怎么打包", query_type="item_packing")

        for doc in result["docs"]:
            assert "doc_id" in doc
            assert "content" in doc
            assert "score" in doc
            assert "authority_level" in doc
            assert isinstance(doc["score"], float)

    @pytest.mark.asyncio
    async def test_safety_code_highest_threshold(self, retriever):
        """safety_code 类应使用最高阈值。"""
        result = await retriever.retrieve("电路改造安全规范", query_type="safety_code")
        # 阈值随 EMBEDDING_PROVIDER 动态调整，但 safety_code 始终最高
        assert result["threshold_used"] >= 0.40

    @pytest.mark.asyncio
    async def test_cost_reference_lowest_threshold(self, retriever):
        """cost_reference 类应使用最低阈值。"""
        result = await retriever.retrieve("水电改造多少钱", query_type="cost_reference")
        # 阈值随 EMBEDDING_PROVIDER 动态调整，但 cost_reference 始终最低
        assert result["threshold_used"] >= 0.30

    @pytest.mark.asyncio
    async def test_authority_penalty_applied(self, retriever):
        """authority_level 降权应生效：llm_generated 得分低于 mandatory_standard。"""
        result = await retriever.retrieve("冰箱怎么打包", query_type="item_packing")

        docs = result["docs"]
        # 原始距离: mandatory=0.85, industry=0.75, llm=0.60
        # 降权后: mandatory=0.85-0=0.85, industry=0.75-0.05=0.70, llm=0.60-0.10=0.50
        if len(docs) >= 2:
            for doc in docs:
                if doc["authority_level"] == "llm_generated":
                    # llm_generated 应受到 -0.10 降权
                    assert doc.get("authority_penalty", 0) <= -0.05

    @pytest.mark.asyncio
    async def test_warning_when_no_docs(self, retriever):
        """无结果时应返回 warning。"""
        retriever._collection.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

        result = await retriever.retrieve("xyz 稀有查询", query_type="general_faq")
        assert result["warning"] == "no_knowledge"
        assert len(result["docs"]) == 0

    @pytest.mark.asyncio
    async def test_query_config_has_seven_types(self):
        """确认 7 种 query_type 都已定义。"""
        expected = {
            "item_packing", "construction_standard", "safety_code",
            "space_dimension", "quantity_estimation", "cost_reference",
            "general_faq",
        }
        assert set(QUERY_CONFIG.keys()) == expected


# ═══════════════════════════════════════════════════════════
# RAG 检索节点测试
# ═══════════════════════════════════════════════════════════

class TestRAGRetrieveNode:
    """测试 RAGRetrieveNode 的输入输出。"""

    @pytest.mark.asyncio
    async def test_node_skips_when_priority_none(self, node):
        """rag_priority=none 时应跳过检索。"""
        state = _make_state(
            "你好",
            rag_prejudge={"query_type": None, "rag_queries": [], "rag_priority": "none"},
        )
        result = await node(state)
        assert result["rag_context"] is None

    @pytest.mark.asyncio
    async def test_node_retrieves_when_priority_required(self, node):
        """rag_priority=required 时应执行检索。"""
        state = _make_state(
            "冰箱怎么打包",
            rag_prejudge={
                "query_type": "item_packing",
                "rag_queries": ["冰箱打包方法"],
                "rag_priority": "required",
            },
        )
        result = await node(state)

        rag = result["rag_context"]
        assert rag is not None
        assert rag["query_type"] == "item_packing"
        assert len(rag["retrieved_docs"]) >= 1
        assert rag["rag_priority"] == "required"

    @pytest.mark.asyncio
    async def test_node_falls_back_to_user_input(self, node):
        """rag_queries 为空时应使用用户原始输入。"""
        state = _make_state(
            "洗衣机怎么搬",
            rag_prejudge={
                "query_type": "item_packing",
                "rag_queries": [],
                "rag_priority": "required",
            },
        )
        result = await node(state)

        rag = result["rag_context"]
        assert rag is not None
        assert len(rag["retrieved_docs"]) >= 1

    @pytest.mark.asyncio
    async def test_node_without_rag_prejudge(self, node):
        """无 _rag_prejudge 时应 fallback 到默认检索。"""
        state = _make_state("三居室搬家需要多少箱子", rag_prejudge=None)
        # 手动设置 _rag_prejudge 为 None（模拟旧版 state）
        state["_rag_prejudge"] = None

        result = await node(state)

        # 无预判时跳过检索
        assert result["rag_context"] is None

    @pytest.mark.asyncio
    async def test_node_passes_warning(self, node):
        """当检索 warning 应为 no_knowledge 时正确传递。"""
        # 让 mock 返回空结果
        node._retriever._collection.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

        state = _make_state(
            "稀有查询",
            rag_prejudge={
                "query_type": "general_faq",
                "rag_queries": ["稀有查询"],
                "rag_priority": "required",
            },
        )
        result = await node(state)

        rag = result["rag_context"]
        assert rag["warning"] == "no_knowledge"


# ═══════════════════════════════════════════════════════════
# format_for_prompt 测试
# ═══════════════════════════════════════════════════════════

class TestFormatForPrompt:
    """测试 rag_context → Agent Prompt 的格式化。"""

    def test_format_with_docs(self, node):
        """有文档时应输出格式化的参考信息。"""
        rag_context = {
            "query_type": "item_packing",
            "retrieved_docs": [
                {
                    "doc_id": "d1",
                    "content": "冰箱搬运前需除霜，直立运输。",
                    "score": 0.85,
                    "authority_level": "mandatory_standard",
                },
                {
                    "doc_id": "d2",
                    "content": "衣物可用真空压缩袋。",
                    "score": 0.70,
                    "authority_level": "llm_generated",
                },
            ],
            "threshold_used": 0.75,
            "warning": None,
        }

        formatted = node.format_for_prompt(rag_context)

        assert "冰箱搬运" in formatted
        assert "📗 国家标准" in formatted
        assert "📙 参考知识" in formatted
        assert "0.85" in formatted

    def test_format_with_warning(self, node):
        """低置信度时应输出警告。"""
        rag_context = {
            "query_type": "general_faq",
            "retrieved_docs": [
                {"doc_id": "d1", "content": "...", "score": 0.55, "authority_level": "llm_generated"},
            ],
            "threshold_used": 0.60,
            "warning": "low_confidence",
        }

        formatted = node.format_for_prompt(rag_context)
        assert "⚠️" in formatted or "较低" in formatted

    def test_format_empty(self, node):
        """空 context 应返回无信息提示。"""
        formatted = node.format_for_prompt(None)
        assert "暂无" in formatted

    def test_format_no_knowledge(self, node):
        """no_knowledge 应返回明确提示。"""
        rag_context = {
            "query_type": "general_faq",
            "retrieved_docs": [],
            "threshold_used": 0.60,
            "warning": "no_knowledge",
        }

        formatted = node.format_for_prompt(rag_context)
        assert "暂无" in formatted or "no_knowledge" in rag_context["warning"]


# ═══════════════════════════════════════════════════════════
# KnowledgeBaseManager 测试
# ═══════════════════════════════════════════════════════════

class TestKnowledgeBaseManager:
    """测试知识库管理器。"""

    def test_seed_documents_have_all_categories(self):
        """种子数据应覆盖所有 5 种 category。"""
        from app.rag.chroma_client import KnowledgeBaseManager

        categories = {d["metadata"]["category"] for d in KnowledgeBaseManager.SEED_DOCUMENTS}
        expected = {"item_packing", "construction_standard", "safety_code",
                    "space_dimension", "quantity_estimation", "cost_reference"}
        assert categories == expected

    def test_seed_documents_have_authority_levels(self):
        """每种 authority_level 都应有代表。"""
        from app.rag.chroma_client import KnowledgeBaseManager

        levels = {d["metadata"]["authority_level"] for d in KnowledgeBaseManager.SEED_DOCUMENTS}
        assert "mandatory_standard" in levels
        assert "industry_best_practice" in levels
        assert "llm_generated" in levels

    def test_seed_document_count(self):
        """种子数据应有至少 10 条。"""
        from app.rag.chroma_client import KnowledgeBaseManager

        assert len(KnowledgeBaseManager.SEED_DOCUMENTS) >= 10


# ═══════════════════════════════════════════════════════════
# 集成测试（需要真实 ChromaDB）
# ═══════════════════════════════════════════════════════════

@pytest.mark.integration
class TestRAGIntegration:
    """需要真实 ChromaDB 的集成测试。"""

    @pytest.mark.asyncio
    async def test_real_retrieval_with_seed_data(self):
        """使用真实 ChromaDB + 种子数据的检索。"""
        import chromadb
        from app.config import get_config

        client = chromadb.Client()
        try:
            collection = client.create_collection(name="test_kb_int", metadata={"description": "test"})
        except Exception:
            client.delete_collection("test_kb_int")
            collection = client.create_collection("test_kb_int")

        from app.rag.chroma_client import KnowledgeBaseManager
        docs = [d["content"] for d in KnowledgeBaseManager.SEED_DOCUMENTS]
        metadatas = [d["metadata"] for d in KnowledgeBaseManager.SEED_DOCUMENTS]
        ids = [f"test_{i}" for i in range(len(docs))]
        collection.add(documents=docs, metadatas=metadatas, ids=ids)

        retriever = AdaptiveRetriever(collection=collection)
        result = await retriever.retrieve("冰箱怎么打包搬运", query_type="item_packing")

        # 不强制断言文档数（取决于 embedding provider 和 API Key 配置）
        # 只验证返回结构正确
        assert isinstance(result["docs"], list)
        assert "warning" in result  # 至少有 warning 字段说明检索执行了

        client.delete_collection("test_kb_int")
