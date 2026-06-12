"""
LangChain BaseTool 封装层 — 将 MCP 工具包装为 LLM 可调用的 Tool。

提供三个标准 Tool：
- AmapDirectionTool: 高德地图路线规划
- LalamoveEstimateTool: 货拉拉运费估算
- RAGSearchTool: 搬装知识库检索

Agent（ReAct 模式）可通过 tool-calling 自主决定何时调用这些工具。

Usage:
    from app.mcp.tool_wrappers import get_all_tools
    tools = get_all_tools()
    llm_with_tools = llm.bind_tools(tools)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Tool 参数 Schema
# ═══════════════════════════════════════════════════════════════

class AmapDirectionInput(BaseModel):
    """高德路线规划参数。"""
    origin: str = Field(description="起点地址，如'北京朝阳'")
    destination: str = Field(description="终点地址，如'北京海淀'")
    strategy: int = Field(default=0, description="路线策略: 0=速度优先, 2=距离优先")
    truck_length_m: float = Field(default=4.2, description="货车长度（米）")


class LalamoveEstimateInput(BaseModel):
    """货拉拉运费估算参数。"""
    from_addr: str = Field(description="搬出地址")
    to_addr: str = Field(description="搬入地址")
    total_volume_m3: float = Field(description="物品总体积（立方米）")
    has_pet: bool = Field(default=False, description="是否有宠物")
    has_large_items: bool = Field(default=False, description="是否有大件物品")
    large_item_count: int = Field(default=0, description="大件物品数量")
    vehicle_type: str | None = Field(default=None, description="指定车型（可选，通常由工具自动匹配）")


class RAGSearchInput(BaseModel):
    """知识库检索参数。"""
    query: str = Field(description="要检索的自然语言查询")
    query_type: str = Field(
        default="general_faq",
        description="查询类型: item_packing|construction_standard|safety_code|space_dimension|quantity_estimation|cost_reference|general_faq"
    )


# ═══════════════════════════════════════════════════════════════
# Tool 实现
# ═══════════════════════════════════════════════════════════════

class AmapDirectionTool(BaseTool):
    """
    高德地图驾车路线规划工具。

    返回：距离(km)、预计时间(min)、过路费(元)、限行信息。
    优先使用真实高德 API，失败时自动降级到本地距离矩阵模拟。
    """
    name: str = "amap_direction"
    description: str = (
        "查询搬家路线信息：包括距离（公里）、预计时间（分钟）、过路费（元）、"
        "货车限行提醒。用于规划搬出到搬入地的行车路线。"
    )
    args_schema: type[BaseModel] = AmapDirectionInput

    def _run(self, origin: str, destination: str, strategy: int = 0,
             truck_length_m: float = 4.2) -> str:
        """同步调用（内部使用 asyncio）。"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as e:
                    return e.submit(asyncio.run, self._arun(origin, destination, strategy, truck_length_m)).result(timeout=30)
            return asyncio.run(self._arun(origin, destination, strategy, truck_length_m))
        except RuntimeError:
            return asyncio.run(self._arun(origin, destination, strategy, truck_length_m))

    async def _arun(self, origin: str, destination: str, strategy: int = 0,
                    truck_length_m: float = 4.2) -> str:
        """异步调用高德路线规划。"""
        from app.mcp.client_manager import get_mcp_client

        logger.info(f"[Tool:amap] {origin} → {destination} (strategy={strategy})")
        mcp = get_mcp_client()

        try:
            result = await mcp.call_tool(
                "amap_direction",
                {
                    "origin": origin,
                    "destination": destination,
                    "strategy": strategy,
                    "truck_length_m": truck_length_m,
                },
                cache_ttl=7200,  # 2 小时缓存
            )
        except Exception as e:
            logger.error(f"[Tool:amap] Failed: {e}")
            return json.dumps({"error": str(e), "source": "tool_error"}, ensure_ascii=False)

        # 提取关键信息，返回自然语言友好的结果
        source_label = "高德实时数据" if result.get("source") == "real_api" else "本地模拟数据"

        if "routes" in result:
            route = result["routes"][0]
            output = {
                "distance_km": route["distance_km"],
                "duration_min": route["duration_min"],
                "toll_yuan": route["toll_yuan"],
                "restrictions": route.get("restrictions", []),
                "traffic_condition": route.get("traffic_condition", "unknown"),
                "source": source_label,
            }
        else:
            output = {
                "distance_km": result.get("distance_km", 0),
                "duration_min": result.get("duration_min", 0),
                "toll_yuan": result.get("toll_yuan", 0),
                "restrictions": result.get("restrictions", []),
                "source": source_label,
            }

        return json.dumps(output, ensure_ascii=False)


class LalamoveEstimateTool(BaseTool):
    """
    货拉拉运费估算工具。

    返回：推荐车型、容量、运费区间（起步价+里程费+附加费）、价格明细。
    优先使用真实货拉拉 API，失败时自动降级到内置价格表模拟。
    """
    name: str = "lalamove_estimate"
    description: str = (
        "估算搬家运费：根据物品体积、是否有宠物/大件物品，"
        "推荐合适车型并给出运费区间（含起步价、里程费、附加费明细）。"
        "返回车型、容量(m³)、运费最低-最高(元)、费用构成。"
    )
    args_schema: type[BaseModel] = LalamoveEstimateInput

    def _run(self, from_addr: str, to_addr: str, total_volume_m3: float,
             has_pet: bool = False, has_large_items: bool = False,
             large_item_count: int = 0) -> str:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as e:
                    return e.submit(asyncio.run, self._arun(
                        from_addr, to_addr, total_volume_m3, has_pet, has_large_items, large_item_count
                    )).result(timeout=30)
            return asyncio.run(self._arun(from_addr, to_addr, total_volume_m3, has_pet, has_large_items, large_item_count))
        except RuntimeError:
            return asyncio.run(self._arun(from_addr, to_addr, total_volume_m3, has_pet, has_large_items, large_item_count))

    async def _arun(self, from_addr: str, to_addr: str, total_volume_m3: float,
                    has_pet: bool = False, has_large_items: bool = False,
                    large_item_count: int = 0) -> str:
        from app.mcp.client_manager import get_mcp_client

        logger.info(f"[Tool:lalamove] {from_addr}→{to_addr}, vol={total_volume_m3}m³, pet={has_pet}, large={has_large_items}")
        mcp = get_mcp_client()

        try:
            result = await mcp.call_tool(
                "lalamove_estimate",
                {
                    "from_addr": from_addr,
                    "to_addr": to_addr,
                    "total_volume_m3": total_volume_m3,
                    "has_pet": has_pet,
                    "has_large_items": has_large_items,
                    "large_item_count": large_item_count,
                },
                cache_ttl=-1,  # 不缓存（每次可能不同）
            )
        except Exception as e:
            logger.error(f"[Tool:lalamove] Failed: {e}")
            return json.dumps({"error": str(e), "source": "tool_error"}, ensure_ascii=False)

        vehicle = result.get("recommended_vehicle", {})
        price = result.get("price_breakdown", {})
        source_label = "货拉拉实时报价" if result.get("source") == "real_api" else "内置价格表模拟"

        output = {
            "vehicle_type": vehicle.get("type", "未知"),
            "vehicle_capacity_m3": vehicle.get("capacity_m3", 0),
            "pet_friendly": vehicle.get("pet_friendly", False),
            "capacity_fit": vehicle.get("capacity_fit", True),
            "base_price_yuan": price.get("base_price_yuan", 0),
            "distance_fee_yuan": price.get("distance_fee_yuan", 0),
            "surcharges_yuan": price.get("total_surcharges_yuan", 0),
            "surcharge_items": [s.get("name", "") for s in price.get("surcharges", [])],
            "total_estimate_yuan": price.get("total_estimate_yuan", 0),
            "price_range_yuan": price.get("price_range", {}),
            "uncertainty_level": result.get("uncertainty_level", "medium"),
            "source": source_label,
        }

        return json.dumps(output, ensure_ascii=False)


class RAGSearchTool(BaseTool):
    """
    搬装知识库检索工具。

    检索领域知识：物品打包方法、施工规范、安全标准、空间尺寸、费用参考等。
    使用自适应阈值检索，结果会标注权威性等级（国标 > 行业实践 > LLM生成）。
    """
    name: str = "rag_search"
    description: str = (
        "搜索搬装领域知识库，获取专业参考信息：物品打包方法（如冰箱/钢琴怎么搬）、"
        "施工规范（如防水/电路标准）、安全标准（如燃气管道要求）、"
        "空间尺寸建议、费用参考等。当你不确定某件事的正确做法时，先用此工具查询。"
    )
    args_schema: type[BaseModel] = RAGSearchInput

    def _run(self, query: str, query_type: str = "general_faq") -> str:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as e:
                    return e.submit(asyncio.run, self._arun(query, query_type)).result(timeout=30)
            return asyncio.run(self._arun(query, query_type))
        except RuntimeError:
            return asyncio.run(self._arun(query, query_type))

    async def _arun(self, query: str, query_type: str = "general_faq") -> str:
        from app.rag.retriever import AdaptiveRetriever

        logger.info(f"[Tool:rag] query='{query[:80]}', type={query_type}")
        retriever = AdaptiveRetriever()

        try:
            result = await retriever.retrieve(query=query, query_type=query_type)
        except Exception as e:
            logger.error(f"[Tool:rag] Failed: {e}")
            return json.dumps({"error": str(e), "docs": []}, ensure_ascii=False)

        docs = result.get("docs", [])
        warning = result.get("warning")

        # 格式化为 Agent 友好输出
        output = {
            "query_type": query_type,
            "found": len(docs),
            "warning": warning,
            "top_results": [
                {
                    "content": doc["content"][:300],
                    "score": doc["score"],
                    "authority": doc.get("authority_level", "unknown"),
                    "source": doc.get("source", ""),
                    "tags": doc.get("tags", []),
                }
                for doc in docs[:5]
            ],
        }

        if warning == "no_knowledge":
            output["note"] = "知识库中暂无直接相关信息，建议基于通用知识回答并标注不确定性"

        return json.dumps(output, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# 便捷工厂函数
# ═══════════════════════════════════════════════════════════════

def get_all_tools() -> list[BaseTool]:
    """获取所有可用工具（供 Agent 绑定）。"""
    return [
        AmapDirectionTool(),
        LalamoveEstimateTool(),
        RAGSearchTool(),
    ]


def get_tool_by_name(name: str) -> BaseTool | None:
    """按名称获取工具实例。"""
    mapping = {
        "amap_direction": AmapDirectionTool,
        "lalamove_estimate": LalamoveEstimateTool,
        "rag_search": RAGSearchTool,
    }
    cls = mapping.get(name)
    return cls() if cls else None
