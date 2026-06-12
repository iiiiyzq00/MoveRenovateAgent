"""
测试意图分类节点（合并 RAG 预判）。

运行:
    pytest tests/unit/test_intent_classify.py -v
"""

from __future__ import annotations

import pytest

from app.agent.nodes.intent import IntentClassifyNode
from app.agent.state import create_empty_state, MasterState


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def node() -> IntentClassifyNode:
    """创建意图分类节点实例。"""
    return IntentClassifyNode()


def _make_state(user_input: str, conversation_round: int = 0) -> MasterState:
    """快速创建测试状态。"""
    state = create_empty_state(session_id="test_intent", user_id="u1")
    state["last_user_input"] = user_input
    state["conversation_round"] = conversation_round
    return state


# ═══════════════════════════════════════════════════════════
# 意图分类测试（不调 LLM 的部分）
# ═══════════════════════════════════════════════════════════

class TestIntentClassifyRoundZero:
    """测试第 0 轮快速路径（不调 LLM）。"""

    @pytest.mark.asyncio
    async def test_round_zero_always_new_topic(self, node):
        """第 0 轮必定返回 new_topic。"""
        state = _make_state("三居室，有猫，预算15万", conversation_round=0)
        result = await node(state)

        assert result["intent"] == "new_topic"
        assert result["intent_confidence"] == 1.0
        assert result["parsed_entities"] == []

    @pytest.mark.asyncio
    async def test_round_zero_includes_rag_prejudge(self, node):
        """第 0 轮应返回 RAG 预判（关键词匹配）。"""
        state = _make_state("冰箱怎么打包比较安全", conversation_round=0)
        result = await node(state)

        rag = result["_rag_prejudge"]
        assert rag["query_type"] == "item_packing"
        assert rag["rag_priority"] == "required"

    @pytest.mark.asyncio
    async def test_round_zero_no_rag_for_generic_input(self, node):
        """不涉及专业知识的输入不应触发 RAG。"""
        state = _make_state("你好，我想搬家", conversation_round=0)
        result = await node(state)

        rag = result["_rag_prejudge"]
        assert rag["rag_priority"] == "none"

    @pytest.mark.asyncio
    async def test_round_zero_safety_triggers_rag(self, node):
        """涉及安全的输入触发 RAG。"""
        state = _make_state("电路改造要符合什么安全规范", conversation_round=0)
        result = await node(state)

        rag = result["_rag_prejudge"]
        assert rag["query_type"] == "safety_code"
        assert rag["rag_priority"] == "required"

    @pytest.mark.asyncio
    async def test_round_zero_cost_triggers_rag(self, node):
        """涉及费用的输入触发 RAG cost_reference。"""
        state = _make_state("水电改造大概多少钱一平米", conversation_round=0)
        result = await node(state)

        rag = result["_rag_prejudge"]
        assert rag["query_type"] == "cost_reference"


# ═══════════════════════════════════════════════════════════
# 数值过滤测试
# ═══════════════════════════════════════════════════════════

class TestNumericChangeFilter:
    """测试数值变化 <5% 的 NO_CHANGE 过滤。"""

    def test_filter_small_change(self, node):
        """变化 <5% 的 MODIFY 实体应被过滤。"""
        entities = [
            {
                "key": "renovation_state.total_budget_yuan",
                "action": "MODIFY",
                "old_value": 150000,
                "new_value": 152000,  # +1.3%
                "data_type": "numeric",
            },
            {
                "key": "renovation_state.house_area_m2",
                "action": "MODIFY",
                "old_value": 90,
                "new_value": 100,  # +11.1%，应保留
                "data_type": "numeric",
            },
        ]
        state = create_empty_state()
        filtered = node._filter_no_change(entities, state)

        # 只保留第二个（面积变化 >5%）
        assert len(filtered) == 1
        assert filtered[0]["key"] == "renovation_state.house_area_m2"

    def test_filter_exact_same(self, node):
        """完全相同的值应被过滤。"""
        entities = [
            {
                "key": "renovation_state.total_budget_yuan",
                "action": "MODIFY",
                "old_value": 150000,
                "new_value": 150000,
                "data_type": "numeric",
            },
        ]
        state = create_empty_state()
        filtered = node._filter_no_change(entities, state)
        assert len(filtered) == 0

    def test_non_numeric_passes_through(self, node):
        """非数值实体不受过滤影响。"""
        entities = [
            {
                "key": "moving_state.from_address",
                "action": "MODIFY",
                "old_value": "北京朝阳",
                "new_value": "北京海淀",
                "data_type": "text",
            },
        ]
        state = create_empty_state()
        filtered = node._filter_no_change(entities, state)
        assert len(filtered) == 1


# ═══════════════════════════════════════════════════════════
# 偏好信号检测测试（风险 7）
# ═══════════════════════════════════════════════════════════

class TestPreferenceSignalDetection:
    """测试偏好信号词检测（规则引擎）。"""

    def test_keyword_budget_tight(self, node):
        """关键词「预算紧」应被检测。"""
        signals = node._detect_preference_signals("预算紧一点，不要超预算")
        assert len(signals) >= 1
        assert any(s["keyword"] == "预算紧" for s in signals)

    def test_regex_budget_constraint(self, node):
        """"不能超过18万" 应触发正则规则。"""
        signals = node._detect_preference_signals("装修预算不能超过18万")
        assert len(signals) >= 1
        regex_signals = [s for s in signals if s["source"] == "regex_match"]
        assert len(regex_signals) == 1
        assert regex_signals[0]["preference_key"] == "budget_sensitivity"
        assert regex_signals[0]["preference_value"] == "tight"

    def test_keyword_eco_priority(self, node):
        """关键词「环保」应被检测。"""
        signals = node._detect_preference_signals("我比较看重环保和无毒材料")
        assert len(signals) >= 1
        keywords = {s["keyword"] for s in signals}
        assert "环保" in keywords

    def test_keyword_style(self, node):
        """风格关键词应被检测。"""
        signals = node._detect_preference_signals("我喜欢的风格是北欧简约风")
        assert len(signals) >= 1
        keywords = {s["keyword"] for s in signals}
        assert "北欧" in keywords

    def test_no_signal_in_plain_input(self, node):
        """普通输入不应触发偏好信号。"""
        signals = node._detect_preference_signals("从朝阳搬到海淀")
        assert len(signals) == 0


# ═══════════════════════════════════════════════════════════
# 启发式降级测试
# ═══════════════════════════════════════════════════════════

class TestHeuristicFallback:
    """测试 LLM 不可用时的降级逻辑。"""

    def test_fallback_detects_reset(self, node):
        """降级逻辑应检测到 reset 意图。"""
        state = _make_state("全部重算，重新开始", conversation_round=1)
        result = node._heuristic_fallback(state)
        assert result["intent"] == "reset"

    def test_fallback_with_existing_state(self, node):
        """有 entity_index 时应返回 incremental_update。"""
        state = _make_state("再加一个沙发", conversation_round=2)
        # 注入已有状态
        state["moving_state"]["from_address"] = "北京朝阳"
        state["renovation_state"]["total_budget_yuan"] = 150000
        result = node._heuristic_fallback(state)
        assert result["intent"] == "incremental_update"

    def test_fallback_empty_state(self, node):
        """无 entity_index 时应返回 new_topic。"""
        state = _make_state("我要搬家", conversation_round=1)
        result = node._heuristic_fallback(state)
        assert result["intent"] == "new_topic"


# ═══════════════════════════════════════════════════════════
# LLM 集成测试（需要真实 API Key）
# ═══════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestIntentClassifyIntegration:
    """真实 LLM 集成测试（需要有效 API Key）。"""

    async def test_real_llm_intent_classify_new_topic(self, node):
        """真实 LLM: 识别 new_topic。"""
        state = _make_state(
            "三居室，有猫，预算15万，从北京朝阳搬到海淀",
            conversation_round=0,
        )
        # 第 0 轮走快速路径（不调 LLM），测试第 2 轮
        state["conversation_round"] = 2
        state["moving_state"]["from_address"] = "北京朝阳"
        state["renovation_state"]["total_budget_yuan"] = 150000

        result = await node(state)

        assert result["intent"] in ("new_topic", "incremental_update", "clarification")
        assert isinstance(result["intent_confidence"], float)

    async def test_real_llm_incremental_update(self, node):
        """真实 LLM: 识别 incremental_update。"""
        state = _make_state("预算提到18万，再加一架钢琴", conversation_round=2)
        state["moving_state"]["from_address"] = "北京朝阳"
        state["moving_state"]["to_address"] = "北京海淀"
        state["renovation_state"]["total_budget_yuan"] = 150000

        result = await node(state)

        assert result["intent"] in ("incremental_update", "new_topic")

        # 如果 LLM 正确识别为增量
        if result["intent"] == "incremental_update":
            entities = result.get("parsed_entities", [])
            # 应有预算 MODIFY 或 钢琴 APPEND
            entity_keys = [e.get("key") for e in entities]
            assert any("budget" in k for k in entity_keys) or any("inventory" in k for k in entity_keys), \
                f"Expected budget or inventory entities, got: {entity_keys}"

    async def test_real_llm_includes_rag_prejudge(self, node):
        """真实 LLM: 应同时输出 RAG 预判。"""
        state = _make_state("冰箱怎么打包？有什么注意事项", conversation_round=2)
        state["moving_state"]["from_address"] = "北京朝阳"

        result = await node(state)

        rag = result.get("_rag_prejudge", {})
        assert "query_type" in rag
        assert "rag_queries" in rag
        assert "rag_priority" in rag
