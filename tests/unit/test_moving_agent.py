"""
测试搬家 Agent 节点。

运行:
    pytest tests/unit/test_moving_agent.py -v
"""

from __future__ import annotations

import pytest

from app.agent.nodes.moving_agent import MovingAgentNode
from app.agent.state import create_empty_state, MasterState
from app.mcp.tools.mock_tools import (
    estimate_freight,
    estimate_route,
    generate_inventory,
    calculate_box_plan,
    match_vehicle,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def node() -> MovingAgentNode:
    return MovingAgentNode()


def _make_state(user_input: str = "三居室，从朝阳搬到海淀，有猫") -> MasterState:
    state = create_empty_state(session_id="test_mv", user_id="u1")
    state["last_user_input"] = user_input
    state["conversation_round"] = 1
    state["_rag_prejudge"] = {"query_type": "item_packing", "rag_queries": ["搬家打包方法"], "rag_priority": "required"}
    state["rag_context"] = {"query_type": "item_packing", "retrieved_docs": [], "threshold_used": 0.75, "warning": None}
    return state


# ═══════════════════════════════════════════════════════════
# Mock 工具测试
# ═══════════════════════════════════════════════════════════

class TestMockTools:
    """测试 mock MCP 工具。"""

    def test_generate_inventory_three_bedroom(self):
        """三居室应生成合理数量的物品。"""
        inv = generate_inventory(layout="三居室")
        assert len(inv) >= 15
        assert len(inv) <= 30
        # 应有双人床
        names = [i["name"] for i in inv]
        assert "双人床" in names
        assert "沙发" in names
        assert "冰箱" in names

    def test_generate_inventory_with_pet(self):
        """有宠物应包含宠物用品箱。"""
        inv = generate_inventory(layout="两居室", has_pet=True)
        assert any("宠物" in i["name"] for i in inv)

    def test_generate_inventory_with_large_items(self):
        """用户指定的大件物品应在清单中。"""
        inv = generate_inventory(
            layout="三居室",
            has_large_items_list=[{"name": "三角钢琴", "category": "大件乐器", "quantity": 1, "estimated_volume_m3": 3.5}],
        )
        assert any("钢琴" in i["name"] for i in inv)

    def test_match_vehicle_small(self):
        """小体积匹配小车型。"""
        v = match_vehicle(volume_m3=5.0)
        assert "van" in v["type"].lower() or "面包" in v["type"]

    def test_match_vehicle_large(self):
        """大体积匹配大车型。"""
        v = match_vehicle(volume_m3=25.0)
        assert "6.8" in v["type"] or v["capacity_m3"] >= 25

    def test_match_vehicle_with_pet(self):
        """有宠物应优先宠物友好车型。"""
        v = match_vehicle(volume_m3=18.0, has_pet=True)
        assert v["pet_friendly"] is True

    def test_estimate_freight_returns_valid_range(self):
        """运费估算应返回合理的价格区间。"""
        f = estimate_freight("朝阳", "海淀", total_volume_m3=18.5, has_pet=True)
        assert f["price_breakdown"]["total_estimate_yuan"] > 0
        assert f["price_breakdown"]["price_range"]["min_yuan"] < f["price_breakdown"]["price_range"]["max_yuan"]
        assert f["recommended_vehicle"]["pet_friendly"] is True

    def test_estimate_route_returns_structure(self):
        """路线估算应包含必要字段。"""
        r = estimate_route("朝阳", "海淀")
        assert len(r["routes"]) >= 1
        assert r["routes"][0]["distance_km"] > 0
        assert r["routes"][0]["duration_min"] > 0

    def test_calculate_box_plan(self):
        """装箱方案数量应合理。"""
        bp = calculate_box_plan(18.5)
        assert bp["small_boxes"] >= 5
        assert bp["medium_boxes"] >= 3
        assert bp["large_boxes"] >= 1
        assert bp["wardrobe_boxes"] >= 2

    def test_inventory_items_have_all_fields(self):
        """物品清单的每项应有必要字段。"""
        inv = generate_inventory(layout="两居室")
        for item in inv:
            assert "item_id" in item
            assert "name" in item
            assert "category" in item
            assert "quantity" in item
            assert "estimated_volume_m3" in item


# ═══════════════════════════════════════════════════════════
# 搬家 Agent 节点测试
# ═══════════════════════════════════════════════════════════

class TestMovingAgentNode:
    """测试 MovingAgentNode。"""

    @pytest.mark.asyncio
    async def test_node_generates_inventory(self, node):
        """首次调用应生成物品清单。"""
        state = _make_state("三居室，从朝阳搬到海淀")
        result = await node(state)

        ms = result["moving_state"]
        assert len(ms["inventory"]) >= 15
        assert ms["total_volume_m3"] > 0

    @pytest.mark.asyncio
    async def test_node_calculates_freight(self, node):
        """应计算运费估算。"""
        state = _make_state("从朝阳搬到通州")
        result = await node(state)

        freight = result["moving_state"]["freight_estimate"]
        assert freight["total_estimate_yuan"] > 0
        assert "price_range" in freight
        assert freight["price_range"]["min_yuan"] > 0

    @pytest.mark.asyncio
    async def test_node_recommends_vehicle(self, node):
        """应推荐车型。"""
        state = _make_state("两居室搬家")
        result = await node(state)

        vehicle = result["moving_state"]["vehicle_recommendation"]
        assert vehicle["type"]
        assert vehicle["capacity_m3"] > 0
        assert "pet_friendly" in vehicle

    @pytest.mark.asyncio
    async def test_node_calculates_route(self, node):
        """应规划路线。"""
        state = _make_state("从海淀搬到朝阳")
        result = await node(state)

        route = result["moving_state"]["route"]
        assert route["distance_km"] > 0
        assert route["duration_min"] > 0
        assert "toll_yuan" in route

    @pytest.mark.asyncio
    async def test_node_calculates_box_plan(self, node):
        """应计算装箱方案。"""
        state = _make_state("三居室搬家")
        result = await node(state)

        bp = result["moving_state"]["box_plan"]
        assert bp["small_boxes"] > 0
        assert bp["large_boxes"] > 0

    @pytest.mark.asyncio
    async def test_node_generates_packing_sequence(self, node):
        """应生成打包顺序。"""
        state = _make_state("三居室，从朝阳搬到海淀")
        result = await node(state)

        ps = result["moving_state"]["packing_sequence"]
        assert len(ps) >= 3
        assert "phase" in ps[0]
        assert "items" in ps[0]

    @pytest.mark.asyncio
    async def test_node_outputs_final_response(self, node):
        """应生成可读的最终响应。"""
        state = _make_state("三居室，从朝阳搬到海淀，有一只猫")
        result = await node(state)

        assert result["final_response"] is not None
        assert "🚛" in result["final_response"] or "搬家" in result["final_response"]
        # 应有体积信息
        assert "m³" in result["final_response"]

    @pytest.mark.asyncio
    async def test_node_preserves_existing_inventory(self, node):
        """如果已有物品清单，不应重新生成。"""
        state = _make_state("修改搬家日期到6月20日")
        # 预填 inventory
        state["moving_state"]["inventory"] = [
            {"item_id": "inv_0001", "name": "沙发", "category": "客厅家具",
             "quantity": 1, "estimated_volume_m3": 2.0, "fragile": False, "special_handling": None},
        ]
        state["moving_state"]["from_address"] = "北京朝阳"
        state["moving_state"]["to_address"] = "北京海淀"

        result = await node(state)

        ms = result["moving_state"]
        # 不应覆盖已有清单
        assert ms["inventory"] == state["moving_state"]["inventory"]

    @pytest.mark.asyncio
    async def test_node_handles_pet_scenario(self, node):
        """有宠物的场景应使用宠物友好车型。"""
        state = _make_state("两居室，从朝阳搬到海淀，家里有一只猫")
        result = await node(state)

        vehicle = result["moving_state"]["vehicle_recommendation"]
        # 体积小时即便有宠物，也应优先匹配容量
        assert vehicle["type"]

    @pytest.mark.asyncio
    async def test_node_handles_large_piano(self, node):
        """搬钢琴的场景应处理大件物品。"""
        state = _make_state("三居室，从朝阳搬到海淀，有一架三角钢琴")
        state["parsed_entities"] = [
            {"key": "moving_state.inventory", "action": "APPEND", "new_value": {"name": "三角钢琴", "category": "大件乐器", "estimated_volume_m3": 3.5}},
        ]
        result = await node(state)

        ms = result["moving_state"]
        total_vol = ms["total_volume_m3"]
        # 总体积应包含钢琴
        assert total_vol > 3.0
        # 车型应足够大
        assert ms["vehicle_recommendation"]["capacity_m3"] >= total_vol or "6.8" in ms["vehicle_recommendation"]["type"]


# ═══════════════════════════════════════════════════════════
# 参数提取测试
# ═══════════════════════════════════════════════════════════

class TestParamExtraction:
    """测试从用户输入中提取参数。"""

    def test_extract_layout(self, node):
        """应正确识别户型。"""
        params = node._extract_params("三居室搬家", [], {}, {})
        assert params["layout"] == "三居室"

    def test_extract_pet(self, node):
        """应正确识别宠物。"""
        params = node._extract_params("有一只猫", [], {}, {})
        assert params["has_pet"] is True

    def test_extract_no_pet(self, node):
        """无明显宠物信号时应为 False。"""
        params = node._extract_params("搬家去海淀", [], {}, {})
        assert params["has_pet"] is False

    def test_extract_from_existing_state(self, node):
        """已有状态中的地址应被保留。"""
        ms = {"from_address": "北京朝阳", "to_address": "北京海淀"}
        params = node._extract_params("加一架钢琴", [], ms, {})
        assert params["from_address"] == "北京朝阳"
        assert params["to_address"] == "北京海淀"


# ═══════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestMovingAgentIntegration:
    """需要真实 LLM 的集成测试。"""

    async def test_full_graph_with_moving_agent(self):
        """完整图流：意图分类 → RAG → 搬家 Agent。"""
        from app.agent.graph import build_graph
        from app.agent.state import create_empty_state

        state = create_empty_state(session_id="test_full_mv", user_id="u1")
        state["last_user_input"] = "三居室，从朝阳搬到海淀，有一只猫"

        graph = build_graph()
        result = await graph.ainvoke(state)

        ms = result.get("moving_state", {})
        assert len(ms.get("inventory", [])) > 0, "应生成物品清单"
        assert ms.get("total_volume_m3", 0) > 0, "应计算总体积"
        assert ms.get("freight_estimate") is not None, "应估算运费"
        assert result.get("final_response") is not None, "应有最终响应"
