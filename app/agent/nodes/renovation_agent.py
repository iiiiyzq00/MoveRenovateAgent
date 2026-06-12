"""
装修 Agent 节点 — node_renovation_agent。

负责：预算分配 / 施工阶段 / 材料清单 / 空间规划 / 甘特图。

P0 阶段：使用规则引擎生成方案 + LLM 润色。
P2 阶段：集成真实 MCP 工具（材料运输费）。

Usage:
    from app.agent.nodes.renovation_agent import RenovationAgentNode
    node = RenovationAgentNode()
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

logger = logging.getLogger(__name__)

PROMPT_PATH: Path = Path(__file__).resolve().parent.parent / "prompts" / "renovation_agent.md"

# ── 装修预算分配规则 ─────────────────────────────────────

DEFAULT_ALLOCATION = {
    "hard_fixture": {"percentage": 0.50, "label": "硬装", "items": [
        "水电改造", "防水施工", "墙面批荡刷漆", "地面铺砖/地板",
        "吊顶施工", "门窗更换", "厨卫瓷砖"
    ]},
    "soft_furnishing": {"percentage": 0.25, "label": "软装", "items": [
        "窗帘", "灯具", "家具定制", "衣柜", "橱柜", "卫浴洁具"
    ]},
    "appliances": {"percentage": 0.15, "label": "家电", "items": [
        "空调", "冰箱", "洗衣机", "热水器", "烟灶套装", "电视"
    ]},
    "reserve": {"percentage": 0.10, "label": "备用金", "items": ["不可预见费用、建材损耗、设计变更等"]},
}

# ── 施工阶段模板 ─────────────────────────────────────────

CONSTRUCTION_PHASES_TEMPLATE: list[dict[str, Any]] = [
    {"phase": "拆除与清理", "base_days": 5, "deps": [], "key_tasks": ["旧装修拆除", "墙皮铲除", "垃圾清运"]},
    {"phase": "水电改造", "base_days": 10, "deps": ["拆除与清理"], "key_tasks": ["水电布线", "强弱电改造", "水管铺设", "打压测试"]},
    {"phase": "泥瓦施工", "base_days": 12, "deps": ["水电改造"], "key_tasks": ["墙面抹灰", "地面找平", "防水施工", "闭水试验", "瓷砖铺贴"]},
    {"phase": "木工施工", "base_days": 8, "deps": ["泥瓦施工"], "key_tasks": ["吊顶造型", "柜体安装", "门套窗套", "背景墙"]},
    {"phase": "油漆施工", "base_days": 10, "deps": ["木工施工"], "key_tasks": ["墙面刮腻子", "打磨", "底漆", "面漆"]},
    {"phase": "安装阶段", "base_days": 7, "deps": ["油漆施工"], "key_tasks": ["灯具安装", "洁具安装", "开关插座", "橱柜安装", "地板铺设"]},
    {"phase": "竣工验收", "base_days": 3, "deps": ["安装阶段"], "key_tasks": ["全面检查", "水电测试", "空气质量检测", "交付"]},
]

# ── 材料清单模板 ─────────────────────────────────────────

MATERIALS_TEMPLATE: list[dict[str, Any]] = [
    {"phase": "水电改造", "name": "PPR水管", "spec": "DN20 热水管", "quantity": 60, "unit": "米", "unit_price": 15, "notes": "含管件"},
    {"phase": "水电改造", "name": "铜芯电线", "spec": "2.5mm² BV线", "quantity": 200, "unit": "米", "unit_price": 3.5, "notes": "插座回路"},
    {"phase": "水电改造", "name": "铜芯电线", "spec": "4mm² BV线", "quantity": 80, "unit": "米", "unit_price": 5.5, "notes": "空调/热水器回路"},
    {"phase": "水电改造", "name": "86型暗盒", "spec": "PVC", "quantity": 30, "unit": "个", "unit_price": 2, "notes": ""},
    {"phase": "泥瓦施工", "name": "水泥", "spec": "42.5R", "quantity": 80, "unit": "袋(50kg)", "unit_price": 35, "notes": "墙面+地面"},
    {"phase": "泥瓦施工", "name": "河沙", "spec": "中砂", "quantity": 5, "unit": "方", "unit_price": 200, "notes": ""},
    {"phase": "泥瓦施工", "name": "防水涂料", "spec": "JS-II型", "quantity": 4, "unit": "桶(18kg)", "unit_price": 280, "notes": "卫生间+阳台"},
    {"phase": "泥瓦施工", "name": "瓷砖", "spec": "800×800mm 全抛釉", "quantity": 120, "unit": "片", "unit_price": 80, "notes": "客餐厅地面"},
    {"phase": "木工施工", "name": "石膏板", "spec": "9.5mm", "quantity": 30, "unit": "张", "unit_price": 45, "notes": "吊顶造型"},
    {"phase": "木工施工", "name": "木工板", "spec": "18mm E0级", "quantity": 10, "unit": "张", "unit_price": 180, "notes": "柜体"},
    {"phase": "油漆施工", "name": "腻子粉", "spec": "内墙耐水型", "quantity": 20, "unit": "袋(20kg)", "unit_price": 45, "notes": "三遍"},
    {"phase": "油漆施工", "name": "乳胶漆", "spec": "净味五合一 18L", "quantity": 4, "unit": "桶", "unit_price": 380, "notes": "一底两面"},
]


class RenovationAgentNode:
    """
    装修 Agent 节点。

    流程:
    1. 从 state 提取装修参数
    2. 计算预算分配（按默认比例 + 用户偏好微调）
    3. 生成施工阶段（基于面积调整工期）
    4. 生成材料清单（基于户型面积缩放）
    5. 生成 Mermaid 甘特图
    6. 空间规划建议
    7. LLM 润色（可选）
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client
        self._prompt_template: str | None = None

    def _load_prompt(self) -> str:
        if self._prompt_template is None and PROMPT_PATH.exists():
            self._prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
        return self._prompt_template or ""

    async def __call__(self, state: MasterState) -> dict:
        """
        执行装修 Agent。

        Input:  MasterState（读取 renovation_state, moving_state, rag_context, user_profile）
        Output: dict（更新 renovation_state）
        """
        rs = state.get("renovation_state", {})
        ms = state.get("moving_state", {})
        user_input = state.get("last_user_input", "")
        profile = state.get("user_profile") or {}
        parsed_entities = state.get("parsed_entities", [])
        rag_ctx = state.get("rag_context")

        # ── 1. 提取参数 ──
        params = self._extract_params(user_input, parsed_entities, rs, ms, profile)

        # ── 2. 预算分配（含适老化 Skill 约束） ──
        total_budget = params.get("total_budget_yuan", 150000)
        active_skills = state.get("active_skills", [])
        budget_allocation = self._allocate_budget(total_budget, params, active_skills)

        # ── 3. 施工阶段 ──
        house_area = params.get("house_area_m2", 90)
        phases, total_days = self._build_phases(house_area)

        # ── 4. 材料清单（含适老化材料） ──
        materials = self._build_materials(house_area, total_budget, params, active_skills)

        # ── 5. 甘特图 ──
        gantt = self._build_gantt(phases)

        # ── 6. 空间规划 ──
        space = self._build_space_planning(params, ms)

        # ── 7. 决策溯源 + RAG 知识引用 ──
        from app.trace.evidence_builder import EvidenceBuilder
        eb = EvidenceBuilder()
        traces_for_state: list[str] = []

        # 预算分配依据
        hard = budget_allocation.get("hard_fixture", {})
        traces_for_state.append(f"📊 预算: 硬装¥{hard.get('amount', 0):,} ({hard.get('percentage', 0):.0%})")

        # 施工阶段依据
        total_days = sum(p["duration_days"] for p in phases)
        traces_for_state.append(f"📅 工期: 预计 {total_days} 天 ({len(phases)}阶段)")

        # 材料清单依据
        traces_for_state.append(f"📦 材料: {len(materials)} 项核心材料")

        # RAG 知识引用 — 将检索到的知识库文档匹配到施工阶段
        rag_citations = self._build_rag_citations(rag_ctx, phases)
        for cite in rag_citations:
            traces_for_state.append(f"📚 {cite['phase']}: {cite['source_ref'][:60]}")

        # ── 8. LLM 润色 ──
        tips: list[str] = []
        notes = ""
        try:
            llm_out = await self._llm_polish(params, budget_allocation, phases, materials, user_input)
            tips = llm_out.get("renovation_tips", [])
            notes = llm_out.get("notes", "")
        except Exception as e:
            logger.warning(f"[reno] LLM polish failed: {e}")

        # ── 9. 构建输出 ──
        current_round = state.get("conversation_round", 0)

        updated_reno: dict[str, Any] = {
            "house_area_m2": house_area,
            "layout": params.get("layout", "三居室"),
            "move_in_condition": params.get("move_in_condition", "毛坯"),
            "total_budget_yuan": total_budget,
            "budget_allocation": budget_allocation,
            "construction_phases": phases,
            "materials": materials,
            "style_preference": params.get("style_preference"),
            "special_needs": params.get("special_needs"),
            "last_updated_round": current_round,
        }

        response = self._build_response(budget_allocation, phases, total_days, gantt, materials, space, tips, notes, rag_citations)

        return {
            "renovation_state": updated_reno,
            "_renovation_response": response,
            "_reno_traces": traces_for_state,  # 暂存，merge 节点合并
            "updated_at": datetime.now().isoformat(),
        }

    # ── 参数提取 ──────────────────────────────────────

    def _extract_params(
        self,
        user_input: str,
        parsed_entities: list[dict[str, Any]],
        reno_state: dict[str, Any],
        moving_state: dict[str, Any],
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        """从用户输入和现有状态提取装修参数。"""
        prefs = profile.get("preferences", profile)

        params: dict[str, Any] = {
            "house_area_m2": reno_state.get("house_area_m2") or 90.0,
            "layout": reno_state.get("layout") or moving_state.get("layout") or "三居室",
            "move_in_condition": reno_state.get("move_in_condition") or "毛坯",
            "total_budget_yuan": reno_state.get("total_budget_yuan") or 150000,
            "style_preference": reno_state.get("style_preference") or prefs.get("style", {}).get("value"),
            "special_needs": reno_state.get("special_needs") or prefs.get("special_needs", []),
            "budget_sensitivity": prefs.get("budget_sensitivity", {}).get("value", "balanced"),
            "eco_priority": prefs.get("eco_priority", {}).get("value", False),
            "family_structure": prefs.get("family_structure", {}),
        }

        # 从 parsed_entities 提取
        for e in parsed_entities:
            key = e.get("key", "")
            val = e.get("new_value")
            if not val:
                continue
            if "house_area" in key:
                params["house_area_m2"] = float(val)
            elif "layout" in key:
                params["layout"] = str(val)
            elif "move_in_condition" in key:
                params["move_in_condition"] = str(val)
            elif "total_budget_yuan" in key or "budget" in key:
                params["total_budget_yuan"] = float(val)
            elif "style" in key:
                params["style_preference"] = str(val)
            elif "special_needs" in key:
                params["special_needs"] = val if isinstance(val, list) else [str(val)]

        # 面积检测
        area_match = re.search(r'(\d+)\s*(平|平米|平方米|㎡|m2)', user_input)
        if area_match:
            params["house_area_m2"] = float(area_match.group(1))

        # 预算检测
        budget_match = re.search(r'(\d+)\s*万', user_input)
        if budget_match:
            params["total_budget_yuan"] = float(budget_match.group(1)) * 10000

        # 风格检测
        style_kw = ["简约", "北欧", "日式", "中式", "工业风", "现代", "极简", "轻奢", "欧式", "美式"]
        for s in style_kw:
            if s in user_input:
                params["style_preference"] = s
                break

        # 特殊需求
        if any(kw in user_input for kw in ["适老", "老人", "父母", "年迈"]):
            if "elderly_safe" not in (params.get("special_needs") or []):
                params.setdefault("special_needs", []).append("elderly_safe")
        if any(kw in user_input for kw in ["儿童", "小孩", "宝宝", "婴儿"]):
            if "child_safe" not in (params.get("special_needs") or []):
                params.setdefault("special_needs", []).append("child_safe")

        # 环保检测
        if any(kw in user_input for kw in ["环保", "甲醛", "无毒", "E0", "E1", "零甲醛"]):
            params["eco_priority"] = True

        # 预算敏感度检测
        if "不能超过" in user_input or "控制在" in user_input or "最多" in user_input:
            params["budget_sensitivity"] = "tight"
        if "不差钱" in user_input or "预算充裕" in user_input or "品质优先" in user_input:
            params["budget_sensitivity"] = "flexible"

        return params

    # ── 预算分配 ──────────────────────────────────────

    def _allocate_budget(
        self, total: float, params: dict[str, Any], active_skills: list[str] | None = None
    ) -> dict[str, Any]:
        """按默认比例 + 用户偏好 + Skill 约束微调分配预算。"""
        sensitivity = params.get("budget_sensitivity", "balanced")
        has_elderly = bool(active_skills and "elderly_accessible" in (active_skills or []))

        alloc = {}
        for key, cfg in DEFAULT_ALLOCATION.items():
            pct = cfg["percentage"]
            if key == "reserve":
                if sensitivity == "tight":
                    pct = 0.08
                elif sensitivity == "flexible":
                    pct = 0.15
            elif key == "hard_fixture":
                if sensitivity == "tight":
                    pct = 0.55
                elif has_elderly:
                    pct = 0.60  # 适老化改造增加硬装比例（无障碍设施）

            amount = round(total * pct)
            new_cfg = dict(cfg)
            new_cfg["percentage"] = round(pct, 2)
            del new_cfg["label"]

            # 适老化 → 追加硬装项目和备注
            if has_elderly and key == "hard_fixture":
                new_cfg["items"] = list(new_cfg.get("items", [])) + [
                    "防滑地砖（R10级）", "L型安全扶手（承重≥100kg）",
                    "紧急呼叫按钮（卧室+卫生间）", "无障碍过道（宽度≥90cm）",
                ]
                new_cfg["note"] = "含适老化无障碍设施预算（+10~15%），建议优先使用防滑材料和无高差设计"

            if params.get("eco_priority") and key == "hard_fixture":
                prev = new_cfg.get("note", "")
                new_cfg["note"] = (prev + "；" if prev else "") + "环保材料升级（E0级板材、零甲醛涂料）"

            alloc[key] = {
                "amount": amount,
                "percentage": new_cfg["percentage"],
                "items": new_cfg["items"],
            }
            if "note" in new_cfg:
                alloc[key]["note"] = new_cfg["note"]

        return alloc

    # ── 施工阶段 ──────────────────────────────────────

    def _build_phases(self, area_m2: float) -> tuple[list[dict[str, Any]], int]:
        """基于面积缩放工期。"""
        # 基准面积 90m²，按比例缩放
        scale = area_m2 / 90.0
        scale = max(0.7, min(scale, 2.0))  # 限制在 0.7-2.0 倍

        phases: list[dict[str, Any]] = []
        current_day = 1

        for p in CONSTRUCTION_PHASES_TEMPLATE:
            days = max(3, round(p["base_days"] * scale))
            phases.append({
                "phase": p["phase"],
                "start_day": current_day,
                "duration_days": days,
                "dependencies": p["deps"],
                "key_tasks": p["key_tasks"],
                "status": "pending",
            })
            current_day += days

        total = current_day - 1
        return phases, total

    # ── 材料清单 ──────────────────────────────────────

    def _build_materials(
        self, area_m2: float, budget: float, params: dict[str, Any],
        active_skills: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """基于面积缩放材料数量 + Skill 约束追加。"""
        scale = area_m2 / 90.0
        scale = max(0.5, min(scale, 2.5))
        has_elderly = bool(active_skills and "elderly_accessible" in (active_skills or []))

        materials: list[dict[str, Any]] = []
        for m in MATERIALS_TEMPLATE:
            qty = max(1, round(m["quantity"] * scale))
            total_price = round(qty * m["unit_price"])
            materials.append({
                "phase": m["phase"],
                "name": m["name"],
                "spec": m["spec"],
                "quantity": qty,
                "unit": m["unit"],
                "estimated_unit_price": m["unit_price"],
                "total_price": total_price,
                "notes": m.get("notes", ""),
            })

        # 适老化材料追加
        if has_elderly:
            elderly_mats = [
                {"phase": "泥瓦施工", "name": "防滑地砖", "spec": "R10级 300×300mm", "quantity": max(30, round(40*scale)), "unit": "片", "unit_price": 45, "notes": "卫生间+厨房地面"},
                {"phase": "安装阶段", "name": "L型安全扶手", "spec": "304不锈钢 承重100kg", "quantity": max(2, round(3*scale)), "unit": "套", "unit_price": 280, "notes": "马桶旁+淋浴区"},
                {"phase": "安装阶段", "name": "紧急呼叫按钮", "spec": "无线433MHz", "quantity": 2, "unit": "个", "unit_price": 150, "notes": "卧室+卫生间各一"},
                {"phase": "安装阶段", "name": "感应夜灯", "spec": "LED 红外感应", "quantity": max(2, round(3*scale)), "unit": "个", "unit_price": 80, "notes": "过道+卫生间"},
            ]
            for em in elderly_mats:
                materials.append({**em, "total_price": em["quantity"] * em["unit_price"]})

        # 环保材料升级
        if params.get("eco_priority"):
            for mat in materials:
                if mat["name"] == "乳胶漆":
                    mat["name"] = "零甲醛乳胶漆"
                    mat["estimated_unit_price"] = 580
                    mat["total_price"] = mat["quantity"] * 580
                    mat["notes"] = "环保升级"
                elif mat["name"] == "木工板":
                    mat["name"] = "ENF级木工板"
                    mat["estimated_unit_price"] = 260
                    mat["total_price"] = mat["quantity"] * 260

        return materials

    # ── 甘特图 ────────────────────────────────────────

    def _build_gantt(self, phases: list[dict[str, Any]]) -> str:
        """生成 Mermaid 甘特图。"""
        lines = [
            "```mermaid",
            "gantt",
            "    title 装修施工甘特图",
            "    dateFormat  YYYY-MM-DD",
            "    axisFormat  %m/%d",
        ]

        for p in phases:
            phase_name = p["phase"]
            start = p["start_day"]
            dur = p["duration_days"]
            safe_name = phase_name.replace(" ", "_")
            lines.append(f"    section {phase_name}")
            lines.append(f"    {safe_name} :a{start}, {start}d, {dur}d")

        lines.append("```")
        return "\n".join(lines)

    # ── RAG 知识引用 ────────────────────────────────────

    def _build_rag_citations(
        self,
        rag_ctx: dict | None,
        phases: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        将 RAG 检索到的知识库文档匹配到施工阶段，生成引用标注。

        匹配规则：
        - 防水 → 搜索 "防水" 相关文档 → 标注 GB 50327-2001 §6.3
        - 电路 → 搜索 "电路/导线" → 标注 JGJ 16-2008
        - 燃气 → 搜索 "燃气" → 标注 GB 50028-2006
        - 材料 → 标注参考价格来源

        Returns:
            [{phase, source_ref, content_excerpt, authority}]
        """
        if not rag_ctx or not rag_ctx.get("retrieved_docs"):
            return []

        docs = rag_ctx["retrieved_docs"]
        citations: list[dict[str, Any]] = []

        # 为每个施工阶段匹配相关知识文档
        phase_keywords = {
            "水电改造": ["电路", "导线", "配电", "水电", "管路", "管道"],
            "泥瓦施工": ["防水", "闭水", "瓷砖", "铺砖", "抹灰"],
            "油漆施工": ["涂料", "乳胶漆", "腻子", "甲醛"],
            "木工施工": ["板材", "吊顶", "柜体", "石膏板"],
            "拆除与清理": ["拆除", "承重墙", "结构"],
        }

        for phase_info in phases:
            phase_name = phase_info["phase"]
            keywords = phase_keywords.get(phase_name, [phase_name])

            # 在 RAG 文档中搜索匹配
            matched: list[dict] = []
            for doc in docs:
                content = doc.get("content", "")
                doc_tags = doc.get("tags", [])
                # 检查内容或标签是否包含关键词
                if any(kw in content for kw in keywords) or any(kw in tag for kw in keywords for tag in doc_tags):
                    matched.append(doc)

            if matched:
                # 取最权威的（authority 最高 + score 最高）
                best = sorted(matched, key=lambda d: (
                    0 if d.get("authority_level") == "mandatory_standard" else
                    1 if d.get("authority_level") == "industry_best_practice" else 2,
                    -d.get("score", 0),
                ))[0]

                authority = best.get("authority_level", "unknown")
                authority_label = {
                    "mandatory_standard": "📗 国家标准",
                    "industry_best_practice": "📘 行业实践",
                    "llm_generated": "📙 参考知识",
                    "user_contributed": "📓 用户贡献",
                }.get(authority, "📓 其他")

                # 提取规范编号（如 "GB 50327-2001"）
                import re
                std_ref = ""
                std_match = re.search(r'(GB\s*\d+[.\d]*-?\d*|JGJ\s*\d+[.\d]*-?\d*|CJJ\s*\d+[.\d]*-?\d*)', best["content"])
                if std_match:
                    std_ref = std_match.group(1)

                citations.append({
                    "phase": phase_name,
                    "source_ref": std_ref or best.get("source", "知识库"),
                    "content_excerpt": best["content"][:200],
                    "authority": authority_label,
                    "score": best.get("score", 0),
                })

        return citations

    # ── 空间规划 ──────────────────────────────────────

    def _build_space_planning(
        self, params: dict[str, Any], moving_state: dict[str, Any]
    ) -> dict[str, Any]:
        """基于家庭结构和特殊需求生成空间规划建议。"""
        layout = params.get("layout", "三居室")
        family = params.get("family_structure", {})
        special_needs = params.get("special_needs", []) or []
        large_items = moving_state.get("inventory", [])

        space: dict[str, Any] = {
            "bedrooms": "",
            "living_room": "",
            "special_notes": [],
        }

        has_child = family.get("has_child", False) or "child_safe" in special_needs
        has_elderly = family.get("has_elderly", False) or "elderly_safe" in special_needs

        if has_child:
            space["special_notes"].append("儿童房建议地面铺设软木地板或PVC地垫，所有插座安装保护盖，窗户加装限位器")
        if has_elderly:
            space["special_notes"].append("适老设计：卫生间安装扶手+防滑地砖，过道宽度≥800mm，避免门槛高差")
        # 大件物品空间预留
        for item in large_items:
            name = item.get("name", "")
            if "钢琴" in name:
                space["special_notes"].append(f"建议在客厅或书房为{name}预留 2m×1.5m 位置，周边避免暖气片和阳光直射")

        if "三居室" in layout:
            space["bedrooms"] = "主卧+次卧+客卧/书房，主卧建议留衣帽间区域"
        elif "两居室" in layout:
            space["bedrooms"] = "主卧+次卧，建议次卧考虑多功能（书桌+折叠床）"

        return space

    # ── 响应构建 ──────────────────────────────────────

    def _build_response(
        self,
        budget: dict[str, Any],
        phases: list[dict[str, Any]],
        total_days: int,
        gantt: str,
        materials: list[dict[str, Any]],
        space: dict[str, Any],
        tips: list[str],
        notes: str,
        rag_citations: list[dict[str, Any]] | None = None,
    ) -> str:
        """构建 Markdown 响应。"""
        lines = [
            "## 🏗️ 装修方案\n",
            "### 💰 预算分配\n",
            "| 类别 | 金额 | 比例 |",
            "|------|------|------|",
        ]
        label_map = {"hard_fixture": "硬装", "soft_furnishing": "软装", "appliances": "家电", "reserve": "备用金"}
        total_allocated = 0
        for key, cfg in budget.items():
            label = label_map.get(key, key)
            amount = cfg["amount"]
            total_allocated += amount
            pct = cfg.get("percentage", 0)
            lines.append(f"| {label} | ¥{amount:,} | {pct:.0%} |")
        lines.append(f"| **合计** | **¥{total_allocated:,}** | **100%** |")

        # 硬装明细
        hard = budget.get("hard_fixture", {})
        if hard.get("items"):
            lines.append(f"\n**硬装包含**: {'、'.join(hard['items'][:5])}")
        if hard.get("note"):
            lines.append(f"> {hard['note']}")

        lines.append(f"\n### 📅 施工阶段（预计 {total_days} 天）\n")
        lines.append("| 阶段 | 天数 | 开始日 |")
        lines.append("|------|------|--------|")
        for p in phases:
            lines.append(f"| {p['phase']} | {p['duration_days']} | 第{p['start_day']}天 |")

        lines.append(f"\n### 📊 施工甘特图\n{gantt}\n")

        lines.append(f"### 📦 主要材料清单\n")
        lines.append("| 阶段 | 材料 | 规格 | 数量 | 单价 | 总价 |")
        lines.append("|------|------|------|------|------|------|")
        for m in materials[:10]:
            lines.append(f"| {m['phase']} | {m['name']} | {m['spec']} | {m['quantity']}{m['unit']} | ¥{m['estimated_unit_price']} | ¥{m['total_price']} |")
        if len(materials) > 10:
            lines.append(f"| ... | 共 {len(materials)} 项材料 | ... | ... | ... | ... |")

        if space.get("special_notes"):
            lines.append(f"\n### 🏠 空间规划建议")
            for note in space["special_notes"]:
                lines.append(f"- {note}")

        if tips:
            lines.append(f"\n### 💡 装修建议")
            for tip in tips[:5]:
                lines.append(f"- {tip}")

        if notes:
            lines.append(f"\n> {notes}")

        # ── RAG 知识库引用溯源 ──
        if rag_citations:
            lines.append(f"\n### 📚 参考依据（知识库溯源）\n")
            for cite in rag_citations:
                ref = cite.get("source_ref", "知识库")
                authority = cite.get("authority", "📓")
                excerpt = cite.get("content_excerpt", "")[:120]
                lines.append(
                    f"- **{cite['phase']}**: {authority} `{ref}`\n"
                    f"  > {excerpt}..."
                )

        lines.append(f"\n> 📋 依据: 预算按{_budget_pct_label(budget)}比例分配，施工天数基于{BUDGET_AREA_DEFAULT}m²基准面积估算，实际以施工方报价和工期为准")

        return "\n".join(lines)

    # ── LLM 润色 ──────────────────────────────────────

    async def _llm_polish(
        self,
        params: dict[str, Any],
        budget: dict[str, Any],
        phases: list[dict[str, Any]],
        materials: list[dict[str, Any]],
        user_input: str,
    ) -> dict[str, Any]:
        """LLM 润色装修建议。"""
        if not self.llm:
            return {}

        total_b = sum(c["amount"] for c in budget.values())
        total_d = sum(p["duration_days"] for p in phases)

        prompt = f"""你是装修规划师。基于以下数据，输出装修建议和注意事项。

面积: {params.get('house_area_m2')}m², 户型: {params.get('layout')}
预算: ¥{total_b:,} 工期: {total_d}天
用户需求: {user_input}

输出 JSON:
{{"renovation_tips": ["建议1"], "notes": "总体说明"}}
"""
        raw = await self.llm.chat(prompt)
        try:
            raw = raw.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                if lines[0].startswith("```"): lines = lines[1:]
                if lines and lines[-1].strip() == "```": lines = lines[:-1]
                raw = "\n".join(lines)
            return json.loads(raw)
        except Exception:
            return {"renovation_tips": [], "notes": ""}


# ── 辅助函数 ─────────────────────────────────────────

BUDGET_AREA_DEFAULT = 90


def _budget_pct_label(budget: dict[str, Any]) -> str:
    """预算分配比例摘要。"""
    parts = []
    label = {"hard_fixture": "硬装", "soft_furnishing": "软装", "appliances": "家电", "reserve": "备用金"}
    for k, v in budget.items():
        pct = v.get("percentage", 0)
        parts.append(f"{label.get(k, k)}{pct:.0%}")
    return "+".join(parts)
