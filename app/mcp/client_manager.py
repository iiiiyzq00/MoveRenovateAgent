"""
MCP 工具客户端管理器 — 统一调用接口，含超时/重试/降级/缓存。

支持真实 API 调用 + Mock 降级。

Usage:
    from app.mcp.client_manager import get_mcp_client
    client = get_mcp_client()
    result = await client.call_tool("amap_direction", {"origin": "朝阳", "destination": "海淀"})
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from app.config import get_config
from app.mcp.tools.mock_tools import estimate_freight as mock_freight
from app.mcp.tools.mock_tools import estimate_route as mock_route

logger = logging.getLogger(__name__)

# ── 可选 httpx 导入 ──────────────────────────────────────
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


class MCPClientManager:
    """MCP 多工具客户端管理器。"""

    def __init__(self) -> None:
        cfg = get_config()
        self.timeout = cfg.mcp_timeout_seconds
        self.max_retries = cfg.mcp_max_retries
        self.amap_key = cfg.amap_api_key
        self.lalamove_key = cfg.lalamove_api_key

        # 内存缓存
        self._cache: dict[str, tuple[dict, float]] = {}  # cache_key → (result, expires_at)

    # ── 统一调用接口 ───────────────────────────────────

    async def call_tool(
        self,
        tool_name: str,
        params: dict,
        *,
        cache_ttl: int | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """
        统一工具调用入口。

        Args:
            tool_name: "amap_direction" | "lalamove_estimate"
            params: 工具参数字典
            cache_ttl: 缓存秒数（None=不缓存，-1=会话级，>0=TTL）
            force_refresh: 跳过缓存强制刷新

        Returns:
            ToolResult dict（与 mock_tools 接口兼容）
        """
        # 缓存检查
        if not force_refresh and cache_ttl is not None:
            cache_key = self._make_cache_key(tool_name, params)
            cached = self._get_from_cache(cache_key)
            if cached:
                cached["from_cache"] = True
                return cached

        # 调用
        for attempt in range(self.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._call_impl(tool_name, params),
                    timeout=self.timeout,
                )
                result["from_cache"] = False
                result["source"] = "real_api"

                # 写缓存
                if cache_ttl is not None and cache_ttl >= 0:
                    self._set_cache(self._make_cache_key(tool_name, params), result, cache_ttl)

                return result

            except asyncio.TimeoutError:
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning(f"[MCP] {tool_name} timeout (attempt {attempt+1}), retry in {wait}s")
                    await asyncio.sleep(wait)
                    continue
                logger.error(f"[MCP] {tool_name} failed after {self.max_retries+1} attempts")
                break

            except Exception as e:
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error(f"[MCP] {tool_name} error: {e}")
                break

        # 降级
        logger.warning(f"[MCP] {tool_name} → falling back to mock")
        fallback_result = await self._fallback(tool_name, params)
        fallback_result["from_cache"] = False
        # 写缓存（即降级结果也可缓存，避免重复降级等待）
        if cache_ttl is not None and cache_ttl >= 0:
            self._set_cache(self._make_cache_key(tool_name, params), fallback_result, cache_ttl)
        return fallback_result

    async def _call_impl(self, tool_name: str, params: dict) -> dict:
        """实际调用分发。"""
        if tool_name == "amap_direction":
            return await self._call_amap(params)
        elif tool_name == "lalamove_estimate":
            return await self._call_lalamove(params)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    # ── 高德驾车路线规划 ──────────────────────────────

    async def _call_amap(self, params: dict) -> dict:
        """
        调用高德驾车路线规划 API v3（无需额外开通，所有 Key 均可使用）。

        https://restapi.amap.com/v3/direction/driving

        注意：v4 货车 API 需要额外申请开通，错误码 10012 表示未授权。
        因此改用 v3 驾车 API + 可选货车参数（height/width/length 等忽略）。

        参数:
            origin: 起点地址 (必填)
            destination: 终点地址 (必填)
            strategy: 0-速度优先/2-距离优先/10-避开高速 (可选)
        """
        if not self.amap_key or self.amap_key.startswith("your_"):
            raise ValueError("AMap API key not configured")

        if not HAS_HTTPX:
            raise RuntimeError("httpx not installed")

        origin = params.get("origin", "")
        destination = params.get("destination", "")

        # ── Step 1: 地理编码（地址 → 坐标） ──
        origin_coord = await self._geocode(origin)
        dest_coord = await self._geocode(destination)

        if not origin_coord or not dest_coord:
            raise RuntimeError(f"Geocoding failed: origin={origin}, dest={destination}")

        # ── Step 2: 驾车路线规划 v3 ──
        req_params = {
            "key": self.amap_key,
            "origin": origin_coord,
            "destination": dest_coord,
            "strategy": str(params.get("strategy", 0)),
        }

        async with httpx.AsyncClient(timeout=self.timeout - 1) as client:
            resp = await client.get(
                "https://restapi.amap.com/v3/direction/driving",
                params=req_params,
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "1":
            err_info = data.get("info", "unknown")
            err_code = data.get("infocode", "")
            # 若 v3 也失败 → 尝试 v4 货车 API（可能用户已开通）
            if err_code in ("10012", "10003", "10001"):
                return await self._call_amap_truck(params)
            raise RuntimeError(f"AMap API error: {err_info} (code={err_code})")

        # 解析路线
        route = data.get("route", {})
        paths = route.get("paths", [])

        if not paths:
            raise RuntimeError("AMap returned no routes")

        path = paths[0]
        steps_data = path.get("steps", [])

        return {
            "route_id": f"amap_{hashlib.md5(f'{origin}{destination}'.encode()).hexdigest()[:12]}",
            "distance_km": round(float(path.get("distance", 0)) / 1000, 1),
            "duration_min": round(float(path.get("duration", 0)) / 60),
            "toll_yuan": float(path.get("tolls", 0)),
            "traffic_condition": self._traffic_label(path.get("traffic_condition")),
            "restrictions": [],
            "alternative_start_time": None,
            "steps": [
                {
                    "instruction": s.get("instruction", ""),
                    "distance_km": round(float(s.get("distance", 0)) / 1000, 1),
                }
                for s in steps_data[:8]
            ],
        }

    async def _call_amap_truck(self, params: dict) -> dict:
        """
        高德货车路线规划 API v4（备选，需额外开通）。

        https://restapi.amap.com/v4/direction/truck
        """
        origin = params.get("origin", "")
        destination = params.get("destination", "")

        req_params = {
            "key": self.amap_key,
            "origin": origin,
            "destination": destination,
            "strategy": params.get("strategy", 0),
            "size": 1,
            "height": str(params.get("truck_height_m", 2.5)),
            "length": str(params.get("truck_length_m", 4.2)),
            "weight": str(params.get("truck_weight_ton", 3.0)),
        }

        async with httpx.AsyncClient(timeout=self.timeout - 1) as client:
            resp = await client.get(
                "https://restapi.amap.com/v4/direction/truck",
                params=req_params,
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "1":
            raise RuntimeError(f"AMap Truck API error: {data.get('info', 'unknown')} (code={data.get('errcode')})")

        route_data = data.get("data", {}).get("route", {})
        paths = route_data.get("paths", [])
        if not paths:
            raise RuntimeError("AMap Truck API returned no routes")

        path = paths[0]
        return {
            "route_id": f"amap_{hashlib.md5(f'{origin}{destination}'.encode()).hexdigest()[:12]}",
            "distance_km": round(float(path.get("distance", 0)) / 1000, 1),
            "duration_min": round(float(path.get("duration", 0)) / 60),
            "toll_yuan": float(path.get("tolls", 0)),
            "traffic_condition": "moderate",
            "restrictions": route_data.get("restriction", []),
            "alternative_start_time": None,
            "steps": [
                {"instruction": s.get("instruction", ""), "distance_km": round(float(s.get("distance", 0)) / 1000, 1)}
                for s in path.get("steps", [])[:8]
            ],
        }

    async def _geocode(self, address: str) -> str | None:
        """
        高德地理编码 API — 地址 → 经纬度坐标。

        https://restapi.amap.com/v3/geocode/geo

        Returns:
            "longitude,latitude" 字符串，失败返回 None
        """
        # 地址已经是坐标格式 → 直接返回
        import re
        if re.match(r'^[\d.]+,[\d.]+$', address.strip()):
            return address.strip()

        # 补全短地址（如 "北京朝阳" → "北京市朝阳区"）
        full_address = self._expand_address(address)

        req_params = {
            "key": self.amap_key,
            "address": full_address,
            "city": self._extract_city(full_address),
        }

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    "https://restapi.amap.com/v3/geocode/geo",
                    params=req_params,
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("status") == "1" and data.get("geocodes"):
                return data["geocodes"][0]["location"]

            logger.warning(f"Geocoding failed for '{address}': {data.get('info')}")
            return None

        except Exception as e:
            logger.warning(f"Geocoding error for '{address}': {e}")
            return None

    @staticmethod
    def _extract_city(address: str) -> str:
        """从地址中提取城市名（用于地理编码 city 参数）。"""
        for city in ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "重庆", "天津"]:
            if city in address:
                return city
        return ""

    @staticmethod
    def _expand_address(address: str) -> str:
        """
        补全短地址，提高地理编码成功率。

        "北京朝阳"→"北京市朝阳区", "海淀"→"北京市海淀区", "浦东"→"上海市浦东新区"
        """
        # 已含"区/县/路/街"等 → 不需要补全
        if any(kw in address for kw in ["区", "县", "路", "街", "道", "号", "大厦", "小区"]):
            return address

        # 城市简称补全
        city_map = {
            "北京": "北京市", "上海": "上海市", "广州": "广州市", "深圳": "深圳市",
            "杭州": "杭州市", "成都": "成都市", "武汉": "武汉市", "南京": "南京市",
            "重庆": "重庆市", "天津": "天津市",
        }

        for short, full in city_map.items():
            if address.startswith(short) and len(address) > len(short):
                # "北京朝阳" → "北京市朝阳区"
                rest = address[len(short):]
                if not rest.endswith("区") and not rest.endswith("新区"):
                    rest += "区"
                return full + rest

        # 只有区名（如"海淀"、"浦东"）
        district_extras = {
            "朝阳": "北京市朝阳区", "海淀": "北京市海淀区", "东城": "北京市东城区",
            "西城": "北京市西城区", "通州": "北京市通州区", "大兴": "北京市大兴区",
            "丰台": "北京市丰台区", "石景山": "北京市石景山区",
            "浦东": "上海市浦东新区", "徐汇": "上海市徐汇区", "静安": "上海市静安区",
            "黄浦": "上海市黄浦区", "长宁": "上海市长宁区",
        }
        if address in district_extras:
            return district_extras[address]

        return address

    @staticmethod
    def _traffic_label(value: str | None) -> str:
        """高德路况标签 → 中文。"""
        mapping = {"0": "unknown", "1": "smooth", "2": "moderate", "3": "congested", "4": "severe"}
        return mapping.get(str(value) if value else "", "unknown")

    def _extract_restrictions(self, route_data: dict) -> list[str]:
        """提取限行信息。"""
        restrictions: list[str] = []
        # 高德返回的 restriction 字段
        raw = route_data.get("restriction", [])
        if isinstance(raw, list):
            restrictions = raw
        elif isinstance(raw, str) and raw:
            restrictions = [raw]
        return restrictions

    # ── 货拉拉运费 ─────────────────────────────────────

    async def _call_lalamove(self, params: dict) -> dict:
        """
        货拉拉运费查询。

        有真实 LALAMOVE_API_KEY + LALAMOVE_API_SECRET → 调用真实 API。
        否则 → mock 降级。
        """
        cfg = get_config()
        api_secret = getattr(cfg, 'lalamove_api_secret', '')

        if (not self.lalamove_key or self.lalamove_key.startswith("your_")
                or not api_secret):
            raise ValueError("Lalamove API key/secret not configured, using mock")

        from app.mcp.tools.lalamove_api import call_real_lalamove
        return await call_real_lalamove(params)

    # ── 降级 ──────────────────────────────────────────

    async def _fallback(self, tool_name: str, params: dict) -> dict:
        """工具失败 → 降级到 Mock"""
        if tool_name == "amap_direction":
            route = mock_route(
                from_addr=params.get("origin", params.get("from_addr", "")),
                to_addr=params.get("destination", params.get("to_addr", "")),
                vehicle_length_m=params.get("truck_length_m", 4.2),
            )
            result = route["routes"][0]
            result["source"] = "mock_fallback"
            result["fallback_reason"] = "real_api_unavailable"
            return result

        elif tool_name == "lalamove_estimate":
            result = mock_freight(
                from_addr=params.get("from_addr", ""),
                to_addr=params.get("to_addr", ""),
                total_volume_m3=params.get("total_volume_m3", 0),
                has_pet=params.get("has_pet", False),
                has_large_items=params.get("has_large_items", False),
                large_item_count=params.get("large_item_count", 0),
            )
            result["source"] = "mock_fallback"
            result["fallback_reason"] = "real_api_unavailable"
            return result

        else:
            return {"error": f"Unknown tool: {tool_name}", "source": "error"}

    # ── 缓存 ──────────────────────────────────────────

    def _make_cache_key(self, tool_name: str, params: dict) -> str:
        """生成缓存键。"""
        # 只使用核心参数做 key
        if tool_name == "amap_direction":
            core = f"{tool_name}|{params.get('origin','')}|{params.get('destination','')}|{params.get('strategy',0)}"
        else:
            core = f"{tool_name}|{params.get('from_addr','')}|{params.get('to_addr','')}|{params.get('total_volume_m3',0)}"
        return hashlib.md5(core.encode()).hexdigest()[:16]

    def _get_from_cache(self, cache_key: str) -> dict | None:
        """从内存缓存读取。"""
        if cache_key in self._cache:
            result, expires_at = self._cache[cache_key]
            if time.monotonic() < expires_at:
                return result.copy()
            del self._cache[cache_key]
        return None

    def _set_cache(self, cache_key: str, result: dict, ttl: int) -> None:
        """写入内存缓存。"""
        self._cache[cache_key] = (result.copy(), time.monotonic() + ttl)

    def clear_cache(self) -> None:
        """清空所有缓存。"""
        self._cache.clear()

    # ── LangChain Tool 定义 ───────────────────────────

    def get_tool_definitions(self) -> list[dict]:
        """返回所有工具的函数定义（供 ReAct Agent binding）。"""
        return [
            {
                "name": "amap_direction",
                "description": "获取货车路线距离、时长、收费和限行信息（高德地图）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string", "description": "起点地址"},
                        "destination": {"type": "string", "description": "终点地址"},
                        "strategy": {"type": "integer", "enum": [0, 1, 2], "default": 0, "description": "0=最快,1=避免收费,2=最短"},
                        "truck_height_m": {"type": "number", "default": 2.5},
                        "truck_length_m": {"type": "number", "default": 4.2},
                        "truck_weight_ton": {"type": "number", "default": 3.0},
                    },
                    "required": ["origin", "destination"],
                },
            },
            {
                "name": "lalamove_estimate",
                "description": "获取货拉拉车型推荐与运费估算",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "from_addr": {"type": "string"},
                        "to_addr": {"type": "string"},
                        "total_volume_m3": {"type": "number"},
                        "has_pet": {"type": "boolean", "default": False},
                        "has_large_items": {"type": "boolean", "default": False},
                        "large_item_count": {"type": "integer", "default": 0},
                    },
                    "required": ["from_addr", "to_addr", "total_volume_m3"],
                },
            },
        ]


# 全局单例
_mcp_client: MCPClientManager | None = None


def get_mcp_client() -> MCPClientManager:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClientManager()
    return _mcp_client
