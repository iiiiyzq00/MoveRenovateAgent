"""
测试级联分析 + Skill 激活。

运行:
    pytest tests/unit/test_cascade.py -v
"""

from __future__ import annotations

import pytest

from app.agent.dependency_graph import DependencyGraph, dep_graph
from app.agent.nodes.cascade import CascadeComputeNode
from app.agent.state import create_empty_state


# ═══════════════════════════════════════════════════════════
# 依赖图测试
# ═══════════════════════════════════════════════════════════

class TestDependencyGraph:
    """测试声明式依赖图。"""

    def test_inventory_cascades_to_volume(self):
        """库存变更 → 级联到体积 → 车型 → 运费。"""
        chain = dep_graph.get_cascade_chain("moving_state.inventory")
        assert "moving_state.total_volume_m3" in chain
        assert "moving_state.vehicle_recommendation" in chain
        assert "moving_state.freight_estimate" in chain

    def test_budget_cascades_cross_agent(self):
        """预算变更 → 跨 Agent 影响运费。"""
        chain = dep_graph.get_cascade_chain("renovation_state.total_budget_yuan")
        assert "renovation_state.budget_allocation" in chain
        # cross_agent: budget → freight
        assert "moving_state.freight_estimate" in chain

    def test_from_address_cascades(self):
        """地址变更 → 路线 → 运费。"""
        chain = dep_graph.get_cascade_chain("moving_state.from_address")
        assert "moving_state.route" in chain

    def test_area_cascades_to_phases(self):
        """面积变更 → 施工阶段 + 材料。"""
        chain = dep_graph.get_cascade_chain("renovation_state.house_area_m2")
        assert "renovation_state.construction_phases" in chain
        assert "renovation_state.materials" in chain

    def test_get_affected_modules(self):
        """应按模块正确分组。"""
        modules = dep_graph.get_affected_modules(["moving_state.inventory"])
        assert len(modules["moving"]) >= 3
        assert "moving_state.total_volume_m3" in modules["moving"]

    def test_cross_agent_signals(self):
        """跨 Agent 字段应产生信号。"""
        signals = dep_graph.get_cross_agent_signals(["renovation_state.total_budget_yuan"])
        assert len(signals) >= 1
        assert signals[0]["from_field"] == "renovation_state.total_budget_yuan"

    def test_topo_sort_respects_deps(self):
        """拓扑排序：被依赖者排在前面。"""
        chain = dep_graph.get_cascade_chain("moving_state.inventory")
        inv_idx = chain.index("moving_state.inventory") if "moving_state.inventory" in chain else -1
        vol_idx = chain.index("moving_state.total_volume_m3") if "moving_state.total_volume_m3" in chain else -1
        if inv_idx >= 0 and vol_idx >= 0:
            assert inv_idx < vol_idx, f"inventory should be before volume, got {chain}"


# ═══════════════════════════════════════════════════════════
# 级联分析节点测试
# ═══════════════════════════════════════════════════════════

class TestCascadeComputeNode:
    """测试 CascadeComputeNode。"""

    @pytest.fixture
    def node(self) -> CascadeComputeNode:
        return CascadeComputeNode()

    @pytest.mark.asyncio
    async def test_budget_change_affects_renovation(self, node):
        """预算修改应影响装修模块。"""
        state = create_empty_state(session_id="test", user_id="u1")
        state["conversation_round"] = 2
        state["parsed_entities"] = [
            {"key": "renovation_state.total_budget_yuan", "action": "MODIFY", "old_value": 150000, "new_value": 180000},
        ]

        result = await node(state)
        affected = result["affected_modules"]

        assert "renovation" in affected
        assert len(result.get("cascade_log", [])) >= 1

    @pytest.mark.asyncio
    async def test_inventory_change_affects_moving(self, node):
        """物品变更应影响搬家模块。"""
        state = create_empty_state(session_id="test", user_id="u1")
        state["conversation_round"] = 2
        state["parsed_entities"] = [
            {"key": "moving_state.inventory", "action": "APPEND", "new_value": {"name": "钢琴"}},
        ]

        result = await node(state)
        assert "moving" in result["affected_modules"]

    @pytest.mark.asyncio
    async def test_no_entities_empty_modules(self, node):
        """无实体 → 空 affected_modules。"""
        state = create_empty_state()
        state["parsed_entities"] = []
        result = await node(state)
        assert result["affected_modules"] == []


# ═══════════════════════════════════════════════════════════
# Skill 激活测试
# ═══════════════════════════════════════════════════════════

class TestSkillActivation:
    """测试 SkillActivateNode。"""

    @pytest.fixture
    async def node(self):
        from app.agent.nodes.skill_activate import SkillActivateNode
        from app.skill.registry import SkillRegistry
        reg = SkillRegistry()
        await reg.scan_directory("skills/")
        return SkillActivateNode(registry=reg)

    @pytest.mark.asyncio
    async def test_pet_keyword_activates_pet_relocation(self, node):
        """包含'猫' → 激活 pet_relocation。"""
        state = create_empty_state(session_id="test", user_id="u1")
        state["last_user_input"] = "三居室，从朝阳搬到海淀，家里有一只猫"

        result = await node(state)
        assert "pet_relocation" in result["active_skills"]

    @pytest.mark.asyncio
    async def test_piano_activates_heavy_lifting(self, node):
        """包含'钢琴' → 激活 heavy_lifting。"""
        state = create_empty_state(session_id="test", user_id="u1")
        state["last_user_input"] = "有一架三角钢琴要搬"

        result = await node(state)
        assert "heavy_lifting" in result["active_skills"]

    @pytest.mark.asyncio
    async def test_no_keywords_no_skills(self, node):
        """无关键词 → 空 skills。"""
        state = create_empty_state(session_id="test", user_id="u1")
        state["last_user_input"] = "从朝阳搬到海淀"

        result = await node(state)
        assert result["active_skills"] == []

    @pytest.mark.asyncio
    async def test_skill_context_has_constraints(self, node):
        """激活后 skill_context 应包含约束规则。"""
        state = create_empty_state(session_id="test", user_id="u1")
        state["last_user_input"] = "有一只猫要搬家"

        result = await node(state)
        ctx = result.get("skill_context", "")
        assert "航空箱" in ctx or "宠物笼" in ctx or "PET" in ctx

    @pytest.mark.asyncio
    async def test_negative_example_filtered(self, node):
        """"我属狗"应被负例过滤。"""
        state = create_empty_state(session_id="test", user_id="u1")
        state["last_user_input"] = "我属狗"

        result = await node(state)
        # "狗"匹配 pet_relocation 关键词，但负例"我属狗"应过滤
        assert "pet_relocation" not in result.get("active_skills", [])

    @pytest.mark.asyncio
    async def test_multiple_skills_activated(self, node):
        """多关键词 → 多 Skill 同时激活。"""
        state = create_empty_state(session_id="test", user_id="u1")
        state["last_user_input"] = "有一只猫，还有一架钢琴"

        result = await node(state)
        skills = result["active_skills"]
        assert "pet_relocation" in skills
        assert "heavy_lifting" in skills


# ═══════════════════════════════════════════════════════════
# Skill 注册表测试
# ═══════════════════════════════════════════════════════════

class TestSkillRegistry:
    """测试 SkillRegistry。"""

    @pytest.fixture
    async def registry(self):
        from app.skill.registry import SkillRegistry
        reg = SkillRegistry()
        await reg.scan_directory("skills/")
        return reg

    @pytest.mark.asyncio
    async def test_scan_loads_skills(self, registry):
        """扫描应加载至少 2 个 Skill。"""
        descs = registry.get_short_descs()
        assert len(descs) >= 2
        ids = {d.skill_id for d in descs}
        assert "pet_relocation" in ids
        assert "heavy_lifting" in ids

    @pytest.mark.asyncio
    async def test_get_sop_returns_structure(self, registry):
        """SOP 应有完整结构。"""
        sop = registry.get_sop("pet_relocation")
        assert sop is not None
        assert sop.skill_id == "pet_relocation"
        assert len(sop.constraints) >= 2
        assert len(sop.tools) >= 1

    @pytest.mark.asyncio
    async def test_keyword_index_has_pet_keywords(self, registry):
        """倒排索引应包含宠物关键词。"""
        idx = registry.get_keyword_index()
        assert "猫" in idx
        assert "pet_relocation" in idx["猫"]

    @pytest.mark.asyncio
    async def test_reload_creates_new_version(self, registry):
        """热加载应创建新版本。"""
        new_ver = await registry.reload("pet_relocation")
        assert new_ver is not None
        # 版本栈应保留历史
        assert len(registry._versions["pet_relocation"]) >= 1

    @pytest.mark.asyncio
    async def test_context_binding(self, registry):
        """版本绑定上下文应正确获取并释放。"""
        async with registry.bind_context(["pet_relocation"]) as ctx:
            sop = ctx.get_sop("pet_relocation")
            assert sop is not None
            assert sop.skill_id == "pet_relocation"
        # 上下文退出后引用已释放


# ═══════════════════════════════════════════════════════════
# E2E 图测试
# ═══════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestP1GraphIntegration:
    """P1 图的集成测试。"""

    async def test_new_topic_activates_skill(self):
        """new_topic 流程应激活 Skill。"""
        from app.agent.graph import build_graph
        from app.agent.state import create_empty_state

        state = create_empty_state(session_id="test_p1", user_id="u1")
        state["last_user_input"] = "三居室，90平，有一只猫，预算15万，从朝阳搬到海淀"

        graph = build_graph()
        result = await graph.ainvoke(state)

        skills = result.get("active_skills", [])
        assert "pet_relocation" in skills, f"Expected pet_relocation activated, got {skills}"
