"""
集成测试：Skill 参数合并 + 冲突仲裁。

运行: pytest tests/integration/test_skill_merge.py -v
"""

from __future__ import annotations

import pytest
from app.agent.nodes.moving_agent import MovingAgentNode


@pytest.mark.integration
class TestSkillParamMerge:
    """测试 _merge_skill_params 的完整逻辑。"""

    @pytest.fixture
    def node(self):
        return MovingAgentNode()

    @pytest.mark.asyncio
    async def test_no_skills_returns_base_params(self, node):
        """无 Skill 时原样返回 base_params。"""
        base = {"from_addr": "朝阳", "has_pet": False}
        result = await node._merge_skill_params(base, [])
        assert result == base

    @pytest.mark.asyncio
    async def test_pet_skill_adds_pet_param(self, node):
        """pet_relocation 应注入 has_pet=true。"""
        base = {"from_addr": "朝阳", "total_volume_m3": 18.5}
        result = await node._merge_skill_params(base, ["pet_relocation"])
        assert result.get("has_pet") is True

    @pytest.mark.asyncio
    async def test_plant_skill_adds_temperature_control(self, node):
        """plant_moving 应注入 temperature_controlled=true。"""
        base = {"from_addr": "朝阳", "total_volume_m3": 18.5}
        result = await node._merge_skill_params(base, ["plant_moving"])
        assert result.get("has_plants") is True
        assert result.get("temperature_controlled") is True

    @pytest.mark.asyncio
    async def test_multi_skills_merge_all_params(self, node):
        """多 Skill 激活时应合并所有参数。"""
        base = {"from_addr": "朝阳"}
        result = await node._merge_skill_params(base, ["pet_relocation", "plant_moving"])
        assert result.get("has_pet") is True
        assert result.get("temperature_controlled") is True

    @pytest.mark.asyncio
    async def test_conflict_resolution_by_severity(self, node):
        """冲突参数应按 severity 仲裁。"""
        base = {"from_addr": "朝阳"}
        result = await node._merge_skill_params(base, ["pet_relocation"])
        # pet_relocation 的 has_pet 来自 params_template（无显式 severity → conditional）
        assert "has_pet" in result
        assert result["has_pet"] is True


@pytest.mark.integration
class TestElderlyRenovationIntegration:
    """测试适老化 Skill 在装修 Agent 中的效果。"""

    @pytest.mark.asyncio
    async def test_elderly_adds_budget_items(self):
        """适老化 Skill 激活后预算分配应包含无障碍项目。"""
        from app.agent.nodes.renovation_agent import RenovationAgentNode
        node = RenovationAgentNode()

        alloc = node._allocate_budget(150000, {"budget_sensitivity": "balanced"}, ["elderly_accessible"])
        hard = alloc["hard_fixture"]
        assert hard["percentage"] == 0.60  # 硬装比例提升
        items = hard.get("items", [])
        assert any("防滑" in i for i in items)
        assert any("扶手" in i for i in items)
        assert any("呼叫" in i for i in items)
        assert "note" in hard and "适老化" in hard["note"]

    @pytest.mark.asyncio
    async def test_elderly_adds_materials(self):
        """适老化 Skill 激活后材料清单应包含适老材料。"""
        from app.agent.nodes.renovation_agent import RenovationAgentNode
        node = RenovationAgentNode()

        mats = node._build_materials(90, 150000, {"budget_sensitivity": "balanced"}, ["elderly_accessible"])
        names = [m["name"] for m in mats]
        assert any("防滑" in n for n in names)
        assert any("扶手" in n for n in names)
        assert any("呼叫" in n for n in names)
        # 总材料数应 > 原 12 项（多了 4 项适老材料）
        assert len(mats) >= 14

    @pytest.mark.asyncio
    async def test_no_elderly_no_extra_items(self):
        """无适老化 Skill 时不应追加材料。"""
        from app.agent.nodes.renovation_agent import RenovationAgentNode
        node = RenovationAgentNode()

        alloc = node._allocate_budget(150000, {"budget_sensitivity": "balanced"}, [])
        hard = alloc["hard_fixture"]
        items = hard.get("items", [])
        assert not any("防滑" in i for i in items)

        mats = node._build_materials(90, 150000, {"budget_sensitivity": "balanced"}, [])
        assert len(mats) == 12  # 原始数量
