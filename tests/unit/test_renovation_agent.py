"""
测试装修 Agent 节点。

运行:
    pytest tests/unit/test_renovation_agent.py -v
"""

from __future__ import annotations

import pytest

from app.agent.nodes.renovation_agent import RenovationAgentNode
from app.agent.state import create_empty_state, MasterState


@pytest.fixture
def node() -> RenovationAgentNode:
    return RenovationAgentNode()


def _make_state(user_input: str = "三居室，预算15万") -> MasterState:
    state = create_empty_state(session_id="test_reno", user_id="u1")
    state["last_user_input"] = user_input
    state["conversation_round"] = 1
    state["rag_context"] = {"query_type": "construction_standard", "retrieved_docs": [], "threshold_used": 0.70, "warning": None}
    return state


# ═══════════════════════════════════════════════════════════
# 参数提取测试
# ═══════════════════════════════════════════════════════════

class TestParamExtraction:
    """测试装修参数提取。"""

    def test_extract_budget(self, node):
        """应提取预算数值。"""
        params = node._extract_params("预算15万装修", [], {}, {}, {})
        assert params["total_budget_yuan"] == 150000

    def test_extract_area(self, node):
        """应提取面积。"""
        params = node._extract_params("90平米的房子装修", [], {}, {}, {})
        assert params["house_area_m2"] == 90.0

    def test_extract_style(self, node):
        """应提取风格偏好。"""
        params = node._extract_params("我喜欢北欧风格", [], {}, {}, {})
        assert params["style_preference"] == "北欧"

    def test_extract_elderly(self, node):
        """应检测适老需求。"""
        params = node._extract_params("家里有老人，要注意安全", [], {}, {}, {})
        needs = params.get("special_needs") or []
        assert "elderly_safe" in needs

    def test_extract_child(self, node):
        """应检测儿童需求。"""
        params = node._extract_params("家里有小孩", [], {}, {}, {})
        needs = params.get("special_needs") or []
        assert "child_safe" in needs

    def test_budget_constraint_triggers_tight(self, node):
        """"不能超过" → 预算敏感度 tight。"""
        params = node._extract_params("装修预算不能超过18万", [], {}, {}, {})
        assert params["budget_sensitivity"] == "tight"


# ═══════════════════════════════════════════════════════════
# 预算分配测试
# ═══════════════════════════════════════════════════════════

class TestBudgetAllocation:
    """测试预算分配。"""

    def test_default_allocation(self, node):
        """默认 150k 预算 → 硬装 50% + 软装 25% + 家电 15% + 备用 10%。"""
        params = {"budget_sensitivity": "balanced", "eco_priority": False}
        alloc = node._allocate_budget(150000, params)

        assert alloc["hard_fixture"]["amount"] == 75000
        assert alloc["soft_furnishing"]["amount"] == 37500
        assert alloc["appliances"]["amount"] == 22500
        assert alloc["reserve"]["amount"] == 15000
        total = sum(c["amount"] for c in alloc.values())
        assert total == 150000

    def test_tight_budget_allocation(self, node):
        """预算紧 → 硬装 55% + 备用金 8%。"""
        params = {"budget_sensitivity": "tight", "eco_priority": False}
        alloc = node._allocate_budget(150000, params)

        assert alloc["hard_fixture"]["percentage"] == 0.55
        assert alloc["reserve"]["percentage"] == 0.08

    def test_flexible_budget_allocation(self, node):
        """预算宽 → 备用金 15%。"""
        params = {"budget_sensitivity": "flexible", "eco_priority": False}
        alloc = node._allocate_budget(150000, params)

        assert alloc["reserve"]["percentage"] == 0.15

    def test_eco_priority_adds_note(self, node):
        """环保优先 → 硬装含环保材料备注。"""
        params = {"budget_sensitivity": "balanced", "eco_priority": True}
        alloc = node._allocate_budget(150000, params)

        hard = alloc["hard_fixture"]
        assert "note" in hard
        assert "环保" in hard.get("note", "")

    def test_allocation_sums_to_total(self, node):
        """分配总和等于总预算。"""
        for budget in [100000, 150000, 200000, 300000]:
            alloc = node._allocate_budget(budget, {"budget_sensitivity": "balanced", "eco_priority": False})
            total = sum(c["amount"] for c in alloc.values())
            assert total == budget, f"Budget {budget}: allocated {total}"


# ═══════════════════════════════════════════════════════════
# 施工阶段测试
# ═══════════════════════════════════════════════════════════

class TestConstructionPhases:
    """测试施工阶段生成。"""

    def test_all_phases_present(self, node):
        """应包含全部 7 个阶段。"""
        phases, total = node._build_phases(90)
        assert len(phases) == 7
        assert phases[0]["phase"] == "拆除与清理"
        assert phases[-1]["phase"] == "竣工验收"

    def test_dependencies_correct(self, node):
        """依赖关系应正确：后一阶段依赖前一个。"""
        phases, _ = node._build_phases(90)
        for i in range(1, len(phases)):
            assert phases[i - 1]["phase"] in phases[i]["dependencies"]

    def test_area_affects_duration(self, node):
        """面积影响工期。"""
        _, days_90 = node._build_phases(90)
        _, days_150 = node._build_phases(150)
        assert days_150 > days_90

    def test_total_days_reasonable(self, node):
        """90m² 总工期应在 45-70 天之间。"""
        _, total = node._build_phases(90)
        assert 45 <= total <= 70, f"Expected 45-70 days, got {total}"


# ═══════════════════════════════════════════════════════════
# 甘特图测试
# ═══════════════════════════════════════════════════════════

class TestGanttChart:
    """测试甘特图生成。"""

    def test_gantt_is_mermaid(self, node):
        """甘特图应为 Mermaid 格式。"""
        phases, _ = node._build_phases(90)
        gantt = node._build_gantt(phases)

        assert gantt.startswith("```mermaid")
        assert "gantt" in gantt
        assert gantt.endswith("```")

    def test_gantt_contains_all_phases(self, node):
        """甘特图应包含所有施工阶段。"""
        phases, _ = node._build_phases(90)
        gantt = node._build_gantt(phases)

        for p in phases:
            assert p["phase"] in gantt


# ═══════════════════════════════════════════════════════════
# 节点测试
# ═══════════════════════════════════════════════════════════

class TestRenovationAgentNode:
    """测试完整节点执行。"""

    @pytest.mark.asyncio
    async def test_node_generates_budget(self, node):
        """应生成预算分配。"""
        state = _make_state("三居室，预算15万")
        result = await node(state)

        rs = result["renovation_state"]
        assert rs["total_budget_yuan"] == 150000
        alloc = rs["budget_allocation"]
        assert alloc["hard_fixture"]["amount"] > 0
        assert alloc["reserve"]["amount"] > 0

    @pytest.mark.asyncio
    async def test_node_generates_phases(self, node):
        """应生成施工阶段。"""
        state = _make_state("两居室，120平，预算20万")
        result = await node(state)

        rs = result["renovation_state"]
        phases = rs["construction_phases"]
        assert len(phases) == 7
        assert phases[0]["duration_days"] > 0

    @pytest.mark.asyncio
    async def test_node_generates_materials(self, node):
        """应生成材料清单。"""
        state = _make_state("三居室，预算15万，要环保材料")
        result = await node(state)

        materials = result["renovation_state"]["materials"]
        assert len(materials) >= 10

    @pytest.mark.asyncio
    async def test_node_outputs_final_response(self, node):
        """应生成最终响应。"""
        state = _make_state("三居室，90平，预算15万，北欧风格，有老人")
        result = await node(state)

        response = result.get("final_response", "")
        assert "🏗️" in response or "装修" in response
        assert "预算" in response
        assert "mermaid" in response
        assert "施工" in response

    @pytest.mark.asyncio
    async def test_node_detects_elements_from_input(self, node):
        """应从用户输入中检测风格和特殊需求。"""
        state = _make_state("北欧风格三居室装修，预算20万，有老人有小孩")
        result = await node(state)

        rs = result["renovation_state"]
        assert rs["style_preference"] == "北欧"
        needs = rs["special_needs"] or []
        assert "elderly_safe" in needs
        assert "child_safe" in needs

    @pytest.mark.asyncio
    async def test_node_handles_from_parsed_entities(self, node):
        """应处理 parsed_entities 中的预算修改。"""
        state = _make_state("预算改到18万")
        state["parsed_entities"] = [
            {"key": "renovation_state.total_budget_yuan", "action": "MODIFY", "new_value": 180000}
        ]
        result = await node(state)

        assert result["renovation_state"]["total_budget_yuan"] == 180000

    @pytest.mark.asyncio
    async def test_node_scale_materials_with_area(self, node):
        """大面积应产生更多材料。"""
        state_small = _make_state("60平小户型装修，预算8万")
        result_small = await node(state_small)
        mats_small = result_small["renovation_state"]["materials"]

        state_large = _make_state("150平大户型装修，预算30万")
        result_large = await node(state_large)
        mats_large = result_large["renovation_state"]["materials"]

        # 大面积的材料数量应更多（同一材料的 quantity）
        total_qty_small = sum(m["quantity"] for m in mats_small)
        total_qty_large = sum(m["quantity"] for m in mats_large)
        assert total_qty_large > total_qty_small

    @pytest.mark.asyncio
    async def test_node_eco_upgrades_materials(self, node):
        """环保优先级应升级材料。"""
        state = _make_state("环保装修，预算15万")
        result = await node(state)

        materials = result["renovation_state"]["materials"]
        # 应有零甲醛乳胶漆或 ENF 板材
        names = [m["name"] for m in materials]
        assert any("零甲醛" in n or "ENF" in n for n in names)

    @pytest.mark.asyncio
    async def test_node_space_planning_in_response(self, node):
        """适老需求应在响应中体现。"""
        state = _make_state("三居室，90平，预算15万，有老人")
        result = await node(state)

        response = result.get("final_response", "")
        assert "扶手" in response or "适老" in response or "防滑" in response


# ═══════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestRenovationIntegration:
    """需要真实 LLM 的集成测试。"""

    async def test_full_graph_with_renovation(self):
        """完整图流应产出含装修方案的响应。"""
        from app.agent.graph import build_graph

        state = create_empty_state(session_id="test_full_reno", user_id="u1")
        state["last_user_input"] = "三居室，90平，预算15万，北欧风格"

        graph = build_graph()
        result = await graph.ainvoke(state)

        rs = result.get("renovation_state", {})
        assert rs.get("total_budget_yuan") is not None
        assert rs.get("construction_phases") is not None
        assert len(rs.get("construction_phases", [])) == 7
