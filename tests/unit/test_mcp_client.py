"""
测试 MCP 客户端（真实 API 降级 + Mock fallback + 缓存）。

运行:
    pytest tests/unit/test_mcp_client.py -v
"""

from __future__ import annotations

import pytest

from app.mcp.client_manager import MCPClientManager, get_mcp_client


@pytest.fixture
def client():
    return MCPClientManager()


# ═══════════════════════════════════════════════════════════
# 降级测试（无真实 API Key 时自动降级）
# ═══════════════════════════════════════════════════════════

class TestFallbackToMock:
    """测试真实 API 不可用时的降级。"""

    @pytest.mark.asyncio
    async def test_amap_returns_valid_route(self, client):
        """应返回有效路线数据（真实 API 或降级 mock）。"""
        result = await client.call_tool(
            "amap_direction",
            {"origin": "北京朝阳", "destination": "北京海淀"},
        )

        assert result["distance_km"] > 0
        assert result["duration_min"] > 0
        assert result["source"] in ("real_api", "mock_fallback")

    @pytest.mark.asyncio
    async def test_lalamove_falls_back_to_mock(self, client):
        """无 Lalamove Key → 降级。"""
        result = await client.call_tool(
            "lalamove_estimate",
            {"from_addr": "朝阳", "to_addr": "海淀", "total_volume_m3": 18.5},
        )

        assert result["price_breakdown"]["total_estimate_yuan"] > 0
        assert result["source"] == "mock_fallback"

    @pytest.mark.asyncio
    async def test_fallback_preserves_interface(self, client):
        """降级结果应与真实 API 接口兼容。"""
        result = await client.call_tool(
            "amap_direction",
            {"origin": "朝阳", "destination": "海淀"},
        )

        # 包含必要字段
        assert "distance_km" in result
        assert "duration_min" in result
        assert "route_id" in result


# ═══════════════════════════════════════════════════════════
# 缓存测试
# ═══════════════════════════════════════════════════════════

class TestCaching:
    """测试缓存机制。"""

    @pytest.mark.asyncio
    async def test_cache_serves_cached_result(self, client):
        """缓存命中时应返回相同结果。"""
        result1 = await client.call_tool(
            "amap_direction",
            {"origin": "朝阳", "destination": "海淀"},
            cache_ttl=3600,
        )

        result2 = await client.call_tool(
            "amap_direction",
            {"origin": "朝阳", "destination": "海淀"},
            cache_ttl=3600,
        )

        assert result2.get("from_cache") is True
        assert result2["distance_km"] == result1["distance_km"]

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self, client):
        """force_refresh 应跳过缓存。"""
        result1 = await client.call_tool(
            "amap_direction",
            {"origin": "朝阳", "destination": "海淀"},
            cache_ttl=3600,
        )

        result2 = await client.call_tool(
            "amap_direction",
            {"origin": "朝阳", "destination": "海淀"},
            cache_ttl=3600,
            force_refresh=True,
        )

        assert result2.get("from_cache") is False

    @pytest.mark.asyncio
    async def test_different_params_different_cache(self, client):
        """不同参数应有不同缓存键。"""
        r1 = await client.call_tool("amap_direction", {"origin": "朝阳", "destination": "海淀"}, cache_ttl=3600)
        r2 = await client.call_tool("amap_direction", {"origin": "朝阳", "destination": "通州"}, cache_ttl=3600)

        # 不同目的地 → 不同缓存 → milestone mock 返回不同距离
        assert r2.get("from_cache") is False

    @pytest.mark.asyncio
    async def test_clear_cache(self, client):
        """clear_cache 应清除所有缓存。"""
        await client.call_tool("amap_direction", {"origin": "朝阳", "destination": "海淀"}, cache_ttl=3600)
        client.clear_cache()

        result = await client.call_tool("amap_direction", {"origin": "朝阳", "destination": "海淀"}, cache_ttl=3600)
        assert result.get("from_cache") is False


# ═══════════════════════════════════════════════════════════
# 工具定义测试
# ═══════════════════════════════════════════════════════════

class TestToolDefinitions:
    """测试 LangChain 工具定义。"""

    def test_get_tool_definitions(self, client):
        """应返回 2 个工具定义。"""
        tools = client.get_tool_definitions()
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"amap_direction", "lalamove_estimate"}

    def test_amap_tool_has_required_params(self, client):
        """amap_direction 应有 origin/destination 必填参数。"""
        tool = [t for t in client.get_tool_definitions() if t["name"] == "amap_direction"][0]
        required = tool["parameters"]["required"]
        assert "origin" in required
        assert "destination" in required


# ═══════════════════════════════════════════════════════════
# 全局单例测试
# ═══════════════════════════════════════════════════════════

class TestSingleton:
    """测试全局单例。"""

    def test_get_mcp_client_returns_same_instance(self):
        """多次调用应返回同一实例。"""
        c1 = get_mcp_client()
        c2 = get_mcp_client()
        assert c1 is c2


# ═══════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestRealAMapIntegration:
    """需要真实高德 API Key 的测试。"""

    async def test_real_amap_call(self):
        """真实高德 API 调用测试（需有效 AMAP_API_KEY）。"""
        from app.config import get_config
        cfg = get_config()

        if not cfg.amap_api_key or cfg.amap_api_key.startswith("your_"):
            pytest.skip("No real AMap API key configured")

        client = MCPClientManager()
        result = await client.call_tool(
            "amap_direction",
            {"origin": "北京市朝阳区望京", "destination": "北京市海淀区中关村"},
            cache_ttl=0,  # 不缓存
        )

        assert result["distance_km"] > 0
        assert result["source"] == "real_api"
        print(f"Real AMap: {result['distance_km']}km, {result['duration_min']}min, toll=¥{result['toll_yuan']}")
