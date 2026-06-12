"""
搬家 Agent 节点 — node_moving_agent。

负责：物品清单生成 / 打包方案 / 车型推荐 / 运费估算 / 路线规划。

P0 阶段：使用规则引擎 + LLM 生成方案。
P2 阶段：集成真实 MCP 工具调用。

Usage:
    from app.agent.nodes.moving_agent import MovingAgentNode
    node = MovingAgentNode()
    result = await node(state)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.llm_client import LLMClient
from app.agent.state import MasterState
from app.mcp.tools.mock_tools import (
    calculate_box_plan,
    generate_inventory,
    ITEM_VOLUME_REF,
)

logger = logging.getLogger(__name__)

PROMPT_PATH: Path = Path(__file__).resolve().parent.parent / "prompts" / "moving_agent.md"


class MovingAgentNode:
    """
    搬家 Agent 节点。

    流程:
    1. 如果 inventory 为空 → 基于规则引擎生成初始物品清单
    2. 调用 mock MCP 工具：路线估算 + 运费计算
    3. 计算装箱方案
    4. 可选：LLM 润色方案（如 API Key 可用）
    5. 为关键结论生成 evidence
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client or LLMClient(temperature=0.2)
        self._prompt_template: str | None = None

    def _load_prompt(self) -> str:
        if self._prompt_template is None and PROMPT_PATH.exists():
            self._prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
        return self._prompt_template or ""

    async def __call__(self, state: MasterState) -> dict:
        """
        执行搬家 Agent。

        Input:  MasterState（读取 moving_state, rag_context, skill_context, user_profile）
        Output: dict（更新 moving_state 的所有字段）
        """
        ms = state.get("moving_state", {})
        user_input = state.get("last_user_input", "")
        rag_ctx = state.get("rag_context")
        active_skills = state.get("active_skills", [])
        profile = state.get("user_profile") or {}
        parsed_entities = state.get("parsed_entities", [])

        # ── 1. 从用户输入中提取搬家相关参数 ──
        params = self._extract_params(user_input, parsed_entities, ms, profile)

        # ── 2. 生成/更新物品清单 ──
        inventory = list(ms.get("inventory", []))  # 浅拷贝避免修改原 list
        if not inventory:
            # 首次生成
            inventory = generate_inventory(
                layout=params.get("layout", "三居室"),
                has_pet=params.get("has_pet", False),
                has_large_items_list=params.get("large_items"),
            )
            logger.info(f"[moving] Generated initial inventory: {len(inventory)} items")
        else:
            # 增量：追加 parsed_entities 中的 APPEND 物品
            append_items = params.get("large_items") or []
            for item in append_items:
                name = item.get("name", "")
                # 避免重复
                if not any(i.get("name") == name for i in inventory):
                    vol_ref = ITEM_VOLUME_REF.get(name, {"volume_m3": 2.0, "fragile": True, "special_handling": "大件搬运"})
                    inventory.append({
                        "item_id": f"inv_incr_{len(inventory):04d}",
                        "name": name,
                        "category": item.get("category", "大件物品"),
                        "quantity": item.get("quantity", 1),
                        "estimated_volume_m3": item.get("estimated_volume_m3", vol_ref.get("volume_m3", 2.0)),
                        "fragile": item.get("fragile", vol_ref.get("fragile", True)),
                        "special_handling": item.get("special_handling", vol_ref.get("special_handling")),
                        "room_origin": item.get("room_origin", "用户追加"),
                    })
                    logger.info(f"[moving] Appended item: {name}")

        # ── 3. 计算总体积 ──
        total_volume = sum(
            i.get("estimated_volume_m3", 0) * i.get("quantity", 1)
            for i in inventory
        )
        total_volume = round(total_volume, 1)

        # ── 4. 装箱方案 ──
        box_plan = calculate_box_plan(total_volume)

        # ── 5. 路线估算（MCP 客户端：真实API → 降级Mock） ──
        from app.mcp.client_manager import get_mcp_client
        mcp = get_mcp_client()
        from_addr = params.get("from_address") or ms.get("from_address") or "北京朝阳"
        to_addr = params.get("to_address") or ms.get("to_address") or "北京海淀"

        # AMap 路线（缓存 2h，force_refresh 在 move_date 变更时触发）
        route_result = await mcp.call_tool(
            "amap_direction",
            {
                "origin": from_addr,
                "destination": to_addr,
                "strategy": 0,  # 最快路线
                "truck_height_m": 2.5,
                "truck_length_m": 4.2,
                "truck_weight_ton": 3.0,
            },
            cache_ttl=7200,  # 2h
        )

        # ── 6. 运费估算（MCP 客户端：真实API → 降级Mock） ──
        # Skill 参数自动合并（模块 4）
        # 宠物/大件标记应从当前 state 推断（而非仅本轮消息），增量时不会丢失
        has_pet = (params.get("has_pet", False)
                   or bool(active_skills and "pet_relocation" in active_skills)
                   or any("宠物" in i.get("category", "") for i in inventory))
        # has_large_items 应从当前 inventory 判断（而非仅本轮新实体）
        has_large = bool(params.get("large_items")) or any(
            i.get("estimated_volume_m3", 0) > 2.0 or "钢琴" in i.get("name", "")
            for i in inventory
        )
        large_count = max(len(params.get("large_items") or []),
                          sum(1 for i in inventory if i.get("estimated_volume_m3", 0) > 2.0))
        has_plants = bool(active_skills and "plant_moving" in active_skills)

        # 构建 lalamove 参数，按 Skill 约束自动合并
        lalamove_params: dict[str, Any] = {
            "from_addr": from_addr,
            "to_addr": to_addr,
            "total_volume_m3": total_volume,
            "has_pet": has_pet,
            "has_large_items": has_large,
            "large_item_count": large_count,
        }
        # 合并激活 Skill 的工具参数模板
        lalamove_params = await self._merge_skill_params(lalamove_params, active_skills)
        if has_plants:
            lalamove_params["has_plants"] = True
            lalamove_params["temperature_controlled"] = True

        freight = await mcp.call_tool(
            "lalamove_estimate", lalamove_params, cache_ttl=-1,
        )

        # 提取路线信息（兼容真实API和mock两种返回格式）
        if "distance_km" in route_result:
            # 真实 API 直接返回的扁平结构
            route_info: dict[str, Any] = {
                "routes": [{
                    "route_id": route_result.get("route_id", "route_001"),
                    "distance_km": route_result.get("distance_km", 0),
                    "duration_min": route_result.get("duration_min", 0),
                    "toll_yuan": route_result.get("toll_yuan", 0),
                    "restrictions": route_result.get("restrictions", []),
                }],
            }
        else:
            # mock fallback 返回的嵌套结构
            route_info = route_result

        # ── 7. 车型推荐 ──
        vehicle = freight["recommended_vehicle"]

        # ── 8. 构建打包顺序 ──
        packing_sequence = self._build_packing_sequence(inventory, box_plan)

        # ── 9. 决策溯源 ──
        from app.trace.evidence_builder import EvidenceBuilder
        eb = EvidenceBuilder()
        traces_for_state: list[str] = []

        # 体积计算依据
        vol_evidence = eb.build_calc_evidence(
            formula="sum(item.volume × quantity)",
            inputs={"item_count": len(inventory), "source": "standard_ref + user_input"},
            result=f"{total_volume}m³"
        )
        traces_for_state.append(f"📐 物品总体积: {total_volume}m³ ({len(inventory)}件)")

        # 车型推荐依据
        veh_evidence = eb.build_tool_evidence(
            "vehicle_selector",
            {"type": vehicle["type"], "capacity": vehicle["capacity_m3"], "fit": vehicle["capacity_fit"]},
            f"体积{total_volume}m³ → 推荐{vehicle['type']}(容量{vehicle['capacity_m3']}m³)"
        )
        traces_for_state.append(f"🚛 推荐车型: {vehicle['type']} (容量 {vehicle['capacity_m3']}m³)")

        # 运费依据
        freight_total = freight["price_breakdown"]["total_estimate_yuan"]
        freight_range = freight["price_breakdown"]["price_range"]
        freight_evidence = eb.build_tool_evidence(
            "lalamove_estimate" if freight.get("source") != "mock_fallback" else "freight_calculator",
            {"total": freight_total, "range": freight_range},
            f"运费 ¥{freight_range['min_yuan']}-{freight_range['max_yuan']} (source={freight.get('source','mock')})"
        )
        traces_for_state.append(f"💰 运费: ¥{freight_range['min_yuan']} - ¥{freight_range['max_yuan']}")

        # ── 10. LLM 润色 ──
        moving_tips: list[str] = []
        notes = ""
        try:
            llm_output = await self._llm_generate_response(
                params=params, inventory=inventory, total_volume=total_volume,
                box_plan=box_plan, vehicle=vehicle, freight=freight,
                route_info=route_info, rag_ctx=rag_ctx,
                active_skills=active_skills, user_input=user_input,
            )
            moving_tips = llm_output.get("moving_tips", [])
            notes = llm_output.get("notes", "")
        except Exception as e:
            logger.warning(f"[moving] LLM polish failed: {e}")

        # ── 11. 构建输出 ──
        current_round = state.get("conversation_round", 0)

        updated_moving: dict[str, Any] = {
            "from_address": from_addr,
            "to_address": to_addr,
            "move_date": params.get("move_date"),
            "inventory": inventory,
            "total_volume_m3": total_volume,
            "total_weight_kg": None,
            "box_plan": box_plan,
            "vehicle_recommendation": {
                "type": vehicle["type"],
                "capacity_m3": vehicle["capacity_m3"],
                "pet_friendly": vehicle["pet_friendly"],
                "capacity_fit": vehicle["capacity_fit"],
            },
            "freight_estimate": {
                "total_estimate_yuan": freight["price_breakdown"]["total_estimate_yuan"],
                "price_range": freight["price_breakdown"]["price_range"],
                "breakdown": freight["price_breakdown"],
                "uncertainty": "low" if freight["uncertainty_pct"] <= 20 else "medium",
            },
            "route": {
                "distance_km": route_info["routes"][0]["distance_km"],
                "duration_min": route_info["routes"][0]["duration_min"],
                "toll_yuan": route_info["routes"][0]["toll_yuan"],
                "restrictions": route_info["routes"][0].get("restrictions", []),
            },
            "packing_sequence": packing_sequence,
            "last_updated_round": current_round,
        }

        # ── 11. 生成响应摘要 ──
        response_parts = [
            f"## 🚛 搬家方案\n",
            f"**搬出**: {from_addr} → **搬入**: {to_addr}",
            f"**物品**: {len(inventory)} 件, 总体积 {total_volume}m³",
            f"",
            f"### 📦 装箱方案",
            f"| 箱型 | 数量 |",
            f"|------|------|",
        ]
        for box_type, count in box_plan.items():
            label = box_type.replace("_", " ").title()
            response_parts.append(f"| {label} | {count} |")
        response_parts.append(f"")
        response_parts.append(f"### 🚛 车型推荐")
        response_parts.append(f"**{vehicle['type']}** (容量 {vehicle['capacity_m3']}m³)")
        if not vehicle["capacity_fit"]:
            response_parts.append(f"⚠️ 容量不足！物品 {total_volume}m³ > 车辆 {vehicle['capacity_m3']}m³")
        response_parts.append(f"")
        response_parts.append(f"### 💰 运费估算")
        response_parts.append(
            f"¥{freight['price_breakdown']['price_range']['min_yuan']} - "
            f"¥{freight['price_breakdown']['price_range']['max_yuan']} "
            f"({route_info['routes'][0]['distance_km']}km)"
        )
        source_label = "高德实时路线" if route_result.get("source") == "real_api" else "本地模拟数据"
        source_note = "实际以 APP 为准" if freight.get("source") != "real_api" else "高德地图实时数据"

        # 不确定性标注（模块 6）
        uncertainty_level = freight.get("uncertainty_level", "low")
        if uncertainty_level != "low":
            unc_pct = freight.get("uncertainty_pct", 20)
            response_parts.append(f"> 🟡 运费为估算区间，实际可能上下浮动 ±{unc_pct}%（原因：{uncertainty_level}置信度数据）")
        response_parts.append(f"> 来源: {source_label} | 运费: {source_note}")
        response_parts.append(f"")
        if moving_tips:
            response_parts.append(f"### 💡 搬家建议")
            for tip in moving_tips[:5]:
                response_parts.append(f"- {tip}")

        return {
            "moving_state": updated_moving,
            "final_response": "\n".join(response_parts),
            "recent_traces": traces_for_state,
            "trace_ids": state.get("trace_ids", []) + [v["evidence_id"] for v in [vol_evidence, veh_evidence, freight_evidence]],
            "updated_at": datetime.now().isoformat(),
            "_moving_raw_output": {
                "inventory_count": len(inventory),
                "total_volume_m3": total_volume,
                "vehicle_type": vehicle["type"],
                "freight_total": freight["price_breakdown"]["total_estimate_yuan"],
            },
        }

    # ── 参数提取 ──────────────────────────────────────

    def _extract_params(
        self,
        user_input: str,
        parsed_entities: list[dict[str, Any]],
        moving_state: dict[str, Any],
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        """从用户输入和现有状态中提取搬家参数。"""
        params: dict[str, Any] = {
            "layout": moving_state.get("layout") or "三居室",
            "from_address": moving_state.get("from_address"),
            "to_address": moving_state.get("to_address"),
            "move_date": moving_state.get("move_date"),
            "has_pet": False,
            "large_items": None,
            "occupants": 2,
            "budget_sensitivity": profile.get("budget_sensitivity", {}).get("value", "balanced"),
        }

        # 从 parsed_entities 提取参数
        for e in parsed_entities:
            key = e.get("key", "")
            val = e.get("new_value")

            if "layout" in key and not params.get("layout"):
                params["layout"] = str(val)
            elif "from_address" in key:
                params["from_address"] = str(val) if val else params["from_address"]
            elif "to_address" in key:
                params["to_address"] = str(val) if val else params["to_address"]
            elif "move_date" in key:
                params["move_date"] = str(val) if val else params["move_date"]
            elif "inventory" in key and e.get("action") == "APPEND":
                if params["large_items"] is None:
                    params["large_items"] = []
                if isinstance(val, dict):
                    params["large_items"].append(val)
            elif "house_area" in key:
                params["house_area"] = float(val) if val else None
            elif "occupants" in key:
                params["occupants"] = int(val) if val else 2

        # 检测宠物
        pet_keywords = ["猫", "狗", "宠物", "仓鼠", "鹦鹉", "鱼缸", "兔子", "主子", "毛孩子"]
        if any(kw in user_input for kw in pet_keywords):
            params["has_pet"] = True

        # 检测大件物品（关键词补漏 — LLM 实体提取可能遗漏）
        large_keywords = {
            "钢琴": {"name": "三角钢琴", "category": "大件乐器", "estimated_volume_m3": 3.5, "fragile": True, "special_handling": "专业钢琴搬运"},
            "三角钢琴": {"name": "三角钢琴", "category": "大件乐器", "estimated_volume_m3": 3.5, "fragile": True, "special_handling": "专业钢琴搬运+可能需吊装"},
            "立式钢琴": {"name": "立式钢琴", "category": "大件乐器", "estimated_volume_m3": 2.0, "fragile": True, "special_handling": "专业钢琴搬运"},
            "保险柜": {"name": "保险柜", "category": "大件物品", "estimated_volume_m3": 1.5, "fragile": False, "special_handling": "超重物品，需专业搬运"},
            "大鱼缸": {"name": "大型鱼缸", "category": "大件物品", "estimated_volume_m3": 2.0, "fragile": True, "special_handling": "需排水拆设备，专业水族搬运"},
        }
        for kw, item_info in large_keywords.items():
            if kw in user_input:
                if params.get("large_items") is None:
                    params["large_items"] = []
                if not any(i.get("name") == item_info["name"] for i in params["large_items"]):
                    params["large_items"].append(item_info)

        # 检测户型
        layout_patterns = [
            (r"三[居室]", "三居室"),
            (r"两[居室]", "两居室"),
            (r"一[居室]", "一居室"),
            (r"四[居室]", "四居室"),
            (r"开间|studio", "开间"),
            (r"复式|loft", "复式"),
            (r"别墅", "别墅"),
        ]
        for pattern, layout in layout_patterns:
            if re.search(pattern, user_input):
                params["layout"] = layout
                break

        return params

    # ── 打包顺序 ──────────────────────────────────────

    def _build_packing_sequence(
        self,
        inventory: list[dict[str, Any]],
        box_plan: dict[str, int],
    ) -> list[dict[str, Any]]:
        """生成打包顺序（按房间分阶段）。"""
        # 按 room_origin 分组
        rooms: dict[str, list[dict[str, Any]]] = {}
        for item in inventory:
            room = item.get("room_origin", "其他")
            rooms.setdefault(room, []).append(item)

        phase_order = ["书房", "次卧", "客厅", "餐厅", "主卧", "厨房", "用户指定", "全屋", "其他"]
        sorted_rooms = sorted(rooms.keys(), key=lambda r: (
            phase_order.index(r) if r in phase_order else 99
        ))

        sequence: list[dict[str, Any]] = []
        phase_num = 0
        for room in sorted_rooms:
            phase_num += 1
            room_items = rooms[room]
            item_names = [i["name"] for i in room_items]
            # 分配箱型
            has_fragile = any(i.get("fragile") for i in room_items)
            box_type = "中号箱+气泡膜" if has_fragile else "大号箱"

            sequence.append({
                "phase": f"第{phase_num}步",
                "room": room,
                "items": item_names,
                "box_type": box_type,
                "note": "先搬不常用区域，最后搬常用区域",
            })

        return sequence

    # ── LLM 润色 ──────────────────────────────────────

    async def _llm_generate_response(
        self,
        params: dict[str, Any],
        inventory: list[dict[str, Any]],
        total_volume: float,
        box_plan: dict[str, int],
        vehicle: dict[str, Any],
        freight: dict[str, Any],
        route_info: dict[str, Any],
        rag_ctx: dict[str, Any] | None,
        active_skills: list[str],
        user_input: str,
    ) -> dict[str, Any]:
        """调用 LLM 生成润色后的搬家建议。"""
        if not self.llm:
            return {}

        # 构建简短 prompt
        prompt = f"""你是搬家规划师。基于以下已计算出的数据，输出搬运建议和注意事项。

搬出: {params.get('from_address')} → 搬入: {params.get('to_address')}
户型: {params.get('layout')}
体积: {total_volume}m³
车型: {vehicle['type']} ({vehicle['capacity_m3']}m³)
运费: ¥{freight['price_breakdown']['total_estimate_yuan']}
距离: {route_info['routes'][0]['distance_km']}km
物品: {len(inventory)}件
活跃场景: {active_skills or '无'}
用户需求: {user_input}

输出 JSON:
{{
  "moving_tips": ["建议1", "建议2"],
  "notes": "总体说明(1-2句)"
}}
"""
        raw = await self.llm.chat(prompt)
        try:
            return self._parse_json(raw)
        except Exception:
            return {"moving_tips": [], "notes": ""}

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines)
        return json.loads(raw)

    # ── Skill 参数合并（模块 4）──────────────────────────

    async def _merge_skill_params(self, base_params: dict[str, Any], active_skills: list[str]) -> dict[str, Any]:
        """
        合并激活 Skill 中的工具参数模板。

        规则：
        - 遍历每个激活 Skill 的 tools 列表
        - 提取 lalamove_estimate 的 params_template
        - 合并到 base_params（Skill 参数覆盖默认值）
        - 冲突时按 severity 仲裁：mandatory > warning > conditional
        """
        if not active_skills:
            return base_params

        from app.skill.registry import get_skill_registry
        registry = await get_skill_registry()
        merged = dict(base_params)
        param_sources: dict[str, tuple[str, str]] = {}

        for skill_id in active_skills:
            try:
                sop = registry.get_sop(skill_id)
                if not sop:
                    continue

                for tool_cfg in sop.tools:
                    if tool_cfg.get("tool_name") != "lalamove_estimate":
                        continue
                    ptemplate = tool_cfg.get("params_template", {})
                    cfg_severity = tool_cfg.get("severity", "conditional")

                    for pkey, pval in ptemplate.items():
                        if pkey not in param_sources:
                            merged[pkey] = pval
                            param_sources[pkey] = (skill_id, cfg_severity)
                        else:
                            _, existing_sev = param_sources[pkey]
                            sev_order = {"mandatory": 3, "warning": 2, "conditional": 1}
                            if sev_order.get(cfg_severity, 0) > sev_order.get(existing_sev, 0):
                                merged[pkey] = pval
                                param_sources[pkey] = (skill_id, cfg_severity)
            except Exception as e:
                logger.debug(f"[merge_params] skip {skill_id}: {e}")

        if param_sources:
            logger.info(f"[merge_params] Merged {len(param_sources)} params from skills: {dict(param_sources)}")
        return merged

    # ── Agent 内主动 RAG 检索 ─────────────────────────

    async def search_knowledge(self, query: str) -> list[dict[str, Any]]:
        """Agent 主动检索知识库（如需要了解某物品的打包方法）。"""
        from app.agent.nodes.rag_retrieve import RAGRetrieveNode
        node = RAGRetrieveNode()
        result = await node.retrieve_for_agent(query, query_type="item_packing")
        return result.get("docs", [])
