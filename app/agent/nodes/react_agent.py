"""
ReAct Agent 节点 — node_react_agent。

使用 LangGraph 构建 ReAct 循环，让 LLM 自主决定何时调用哪些工具。

核心流程:
1. 构建 System Prompt（用户需求 + Skill 约束 + RAG 上下文 + 已有状态）
2. LLM 推理 → 决定调哪些工具 → 调工具 → 观察结果 → 继续推理
3. 最终输出自然语言 Markdown 方案
4. 从最终输出中提取结构化数据更新 moving_state / renovation_state

与原有 moving_agent.py 的关系:
- 原 moving_agent 的规则引擎流程被 Agent 自主工具调用替代
- Agent 内部调用相同的 MCP 工具（通过 tool_wrappers 封装）
- 输出保持兼容格式（final_response + moving_state 更新）

Usage:
    from app.agent.nodes.react_agent import ReActAgentNode
    node = ReActAgentNode()
    result = await node(state)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from app.agent.llm_client import LLMClient
from app.agent.state import MasterState, MovingState, RenovationState  # noqa: F401

logger = logging.getLogger(__name__)

# ── ReAct 最大迭代次数 ──
MAX_REACT_ITERATIONS = 6

PROMPT_PATH: Path = Path(__file__).resolve().parent.parent / "prompts" / "react_agent.md"


# ═══════════════════════════════════════════════════════════════
# ReAct 内部状态（子图专用）
# ═══════════════════════════════════════════════════════════════

class ReActState(dict):
    """ReAct Agent 子图内部状态。"""

    @classmethod
    def from_master(cls, state: MasterState) -> "ReActState":
        """从 MasterState 初始化。"""
        return cls(
            messages=[],
            iteration=0,
            tool_call_log=[],
            final_response="",
            master_state=state,
        )


class ReActAgentNode:
    """
    ReAct Agent 节点 — LLM 自主推理 + 工具调用。

    设计要点:
    - 单次 __call__ 内完成 ReAct 循环
    - 工具通过 LangChain Tool 接口调用
    - 最终响应保持 Markdown 格式，兼容下游节点
    - 从响应中提取结构化数据更新 moving_state
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client or LLMClient(temperature=0.3)
        self._prompt_template: str | None = None
        self._tools: list | None = None

    @property
    def tools(self) -> list:
        """懒加载工具列表。"""
        if self._tools is None:
            from app.mcp.tool_wrappers import get_all_tools
            self._tools = get_all_tools()
        return self._tools

    def _load_prompt(self) -> str:
        if self._prompt_template is None and PROMPT_PATH.exists():
            self._prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
        return self._prompt_template or ""

    async def __call__(self, state: MasterState) -> dict:
        """
        执行 ReAct Agent。

        Input:  MasterState（读取 user_input, skill_context, rag_context, user_profile, moving_state）
        Output: dict（更新 final_response, moving_state, tool_call_log, recent_traces）
        """
        user_input = state.get("last_user_input", "")
        skill_context = state.get("skill_context", "")
        rag_ctx = state.get("rag_context")
        active_skills = state.get("active_skills", [])
        profile = state.get("user_profile") or {}
        ms = state.get("moving_state", {})
        rs = state.get("renovation_state", {})
        parsed_entities = state.get("parsed_entities", [])

        # ── 1. 构建 System Prompt ──
        system_prompt = self._build_system_prompt(
            user_input=user_input,
            skill_context=skill_context,
            active_skills=active_skills,
            rag_ctx=rag_ctx,
            profile=profile,
            moving_state=ms,
            renovation_state=rs,
            parsed_entities=parsed_entities,
        )

        # ── 2. 构建消息列表 ──
        messages = [SystemMessage(content=system_prompt)]

        # 如果有之前的响应（增量场景），添加上下文
        prev_response = state.get("final_response", "")
        if prev_response and state.get("conversation_round", 0) > 0:
            messages.append(HumanMessage(
                content=f"[上一轮方案已生成]\n当前用户追加/修改: {user_input}"
            ))
        else:
            messages.append(HumanMessage(content=user_input))

        # ── 3. ReAct 循环 ──
        tool_call_log: list[dict[str, Any]] = []
        iteration = 0

        # 绑定工具到 LLM
        llm_with_tools = self.llm.model.bind_tools(self.tools)

        while iteration < MAX_REACT_ITERATIONS:
            iteration += 1
            logger.info(f"[react] Iteration {iteration}/{MAX_REACT_ITERATIONS}")

            # 调用 LLM
            response: AIMessage = await llm_with_tools.ainvoke(messages)
            messages.append(response)

            # 检查是否有 tool_calls
            tool_calls = getattr(response, "tool_calls", []) or []

            if not tool_calls:
                # 无工具调用 → Agent 已给出最终答案
                logger.info(f"[react] Agent finished after {iteration} iterations (no tool calls)")
                break

            # 执行工具调用
            logger.info(f"[react] Executing {len(tool_calls)} tool calls: "
                        f"{[tc.get('name', '?') for tc in tool_calls]}")

            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {})
                tool_call_id = tc.get("id", f"call_{iteration}")

                # 找到对应工具并执行
                tool_instance = self._find_tool(tool_name)
                if tool_instance is None:
                    result_text = json.dumps({"error": f"Unknown tool: {tool_name}"})
                else:
                    try:
                        # 调用工具的 _arun 方法
                        result_text = await tool_instance._arun(**tool_args)
                    except Exception as e:
                        logger.error(f"[react] Tool '{tool_name}' error: {e}")
                        result_text = json.dumps({"error": str(e), "tool": tool_name})

                # 记录日志（保存完整结果用于结构化提取）
                tool_call_log.append({
                    "iteration": iteration,
                    "tool": tool_name,
                    "args": {k: str(v)[:80] for k, v in tool_args.items()},
                    "result_summary": str(result_text)[:200],
                    "result_json": result_text,  # 完整 JSON，用于结构化提取
                })

                # 将工具结果添加为 ToolMessage
                messages.append(ToolMessage(content=result_text, tool_call_id=tool_call_id))

        # ── 4. 提取最终响应 ──
        final_text = ""
        # 从最后一条 AIMessage 提取文本
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                final_text = msg.content if isinstance(msg.content, str) else str(msg.content)
                break

        if not final_text:
            # 降级：用最后一条消息
            final_text = str(messages[-1].content) if messages else "未能生成方案，请重试。"

        # ── 5. 从工具结果中提取结构化数据（优先，准确度高）──
        tool_data = self._extract_from_tool_results(tool_call_log)
        # 从文本中补充提取（兜底）
        text_data = self._extract_moving_state(final_text, ms)
        text_reno = self._extract_renovation_state(final_text, rs)
        # 合并：工具数据优先，文本数据补充
        moving_updates = {**text_data, **tool_data}  # tool_data 覆盖 text_data
        renovation_updates = text_reno  # 装修从文本提取

        # ── 6. 生成决策溯源 ──
        traces = self._build_traces(tool_call_log, moving_updates)

        # 如果没有工具调用但有激活的 Skill，标注 Skill 为知识来源
        if not tool_call_log and active_skills:
            skill_labels = {
                "pet_relocation": "🐱 宠物搬运 Skill 提供知识",
                "heavy_lifting": "🏗️ 大件吊装 Skill 提供知识",
                "elderly_accessible": "👴 适老化改造 Skill 提供知识",
                "plant_moving": "🌿 绿植搬运 Skill 提供知识",
            }
            for sid in active_skills:
                label = skill_labels.get(sid, f"📋 Skill '{sid}' 提供知识")
                traces.append(label)

        # 如果使用了 RAG 预检索结果
        if rag_ctx and rag_ctx.get("retrieved_docs"):
            traces.append(f"📚 知识库检索: {len(rag_ctx['retrieved_docs'])} 条相关文档")

        # ── 7. 构建输出 ──
        current_round = state.get("conversation_round", 0)

        output: dict[str, Any] = {
            "_moving_response": final_text,
            "_moving_traces": traces,  # 暂存，merge 节点合并
            "moving_state": {**ms, **moving_updates, "last_updated_round": current_round},
            "tool_call_log": tool_call_log,
            "_react_iterations": iteration,
        }

        logger.info(
            f"[react] Completed: {iteration} iterations, {len(tool_call_log)} tool calls, "
            f"response length={len(final_text)}"
        )

        return output

    # ── System Prompt 构建 ──────────────────────────────────

    def _build_system_prompt(
        self,
        user_input: str,
        skill_context: str | None,
        active_skills: list[str],
        rag_ctx: dict | None,
        profile: dict,
        moving_state: dict,
        renovation_state: dict,
        parsed_entities: list[dict],
    ) -> str:
        """构建 ReAct Agent 的 System Prompt。"""
        prompt = self._load_prompt()

        # Skill 约束 + 工具参数模板
        skill_section = ""
        if skill_context:
            # 提取工具参数模板（单独列出，让 LLM 更容易关注）
            tool_params = self._extract_skill_tool_params(skill_context)
            skill_section = (
                f"\n## 当前激活的专业场景\n{skill_context}\n"
                f"### ⚠️ 场景专用工具参数（调用工具时必须使用）\n{tool_params}"
            )
        elif active_skills:
            skill_section = f"\n## 当前激活的专业场景\n{', '.join(active_skills)}"

        # RAG 上下文
        rag_section = ""
        if rag_ctx and rag_ctx.get("retrieved_docs"):
            from app.agent.nodes.rag_retrieve import RAGRetrieveNode
            rag_node = RAGRetrieveNode()
            rag_section = "\n## 知识库预检索结果\n" + rag_node.format_for_prompt(rag_ctx)

        # 已有状态摘要
        state_section = ""
        if moving_state.get("from_address") or renovation_state.get("total_budget_yuan"):
            lines = ["\n## 当前已有方案（增量修改时参考）"]
            if moving_state.get("from_address"):
                lines.append(f"- 搬出: {moving_state.get('from_address')} → 搬入: {moving_state.get('to_address', '?')}")
            if moving_state.get("total_volume_m3"):
                lines.append(f"- 总体积: {moving_state['total_volume_m3']}m³")
            if moving_state.get("vehicle_recommendation"):
                lines.append(f"- 当前车型: {moving_state['vehicle_recommendation'].get('type', '?')}")
            if moving_state.get("freight_estimate"):
                fe = moving_state["freight_estimate"]
                lines.append(f"- 当前运费: ¥{fe.get('total_estimate_yuan', '?')}")
            if renovation_state.get("total_budget_yuan"):
                lines.append(f"- 装修预算: ¥{renovation_state['total_budget_yuan']:,}")
            state_section = "\n".join(lines)

        # 用户画像 → 个性化推荐
        profile_section = ""
        if profile:
            prefs = profile.get("preferences", profile)
            profile_lines = ["\n## 用户画像（长期偏好 — 请据此个性化方案）"]
            if isinstance(prefs, dict):
                for k, v in prefs.items():
                    if isinstance(v, dict):
                        val = v.get("value", v)
                        profile_lines.append(f"- {k}: {val}")
                    else:
                        profile_lines.append(f"- {k}: {v}")
            profile_lines.append("")
            profile_lines.append("**请根据以上偏好调整方案**：")
            if prefs.get("budget_sensitivity", {}).get("value") == "tight":
                profile_lines.append("- 预算紧张 → 推荐性价比方案，多对比价格，备用金降到8%")
            if prefs.get("budget_sensitivity", {}).get("value") == "flexible":
                profile_lines.append("- 预算充裕 → 推荐品质优先方案，可升级车型和材料档次")
            if prefs.get("eco_priority", {}).get("value"):
                profile_lines.append("- 环保优先 → 推荐E0级板材、零甲醛涂料，标注环保等级")
            if prefs.get("style", {}).get("value"):
                profile_lines.append(f"- 风格偏好: {prefs['style']['value']} → 材料选择匹配该风格")
            if prefs.get("family_structure", {}).get("value", {}).get("has_child"):
                profile_lines.append("- 有小孩 → 推荐儿童安全材料，插座保护，圆角家具")
            if prefs.get("family_structure", {}).get("value", {}).get("has_elderly"):
                profile_lines.append("- 有老人 → 推荐适老化设计，防滑地面，扶手安装")
            profile_section = "\n".join(profile_lines) if len(profile_lines) > 3 else ""

        # 实体提取摘要
        entity_section = ""
        if parsed_entities:
            entity_lines = ["\n## 本轮提取的修改/追加"]
            for e in parsed_entities[:8]:
                entity_lines.append(f"- [{e.get('action', '?')}] {e.get('key', '?')} → {str(e.get('new_value', ''))[:80]}")
            entity_section = "\n".join(entity_lines)

        return prompt + skill_section + rag_section + state_section + profile_section + entity_section

    @staticmethod
    def _extract_skill_tool_params(skill_context: str) -> str:
        """
        从 skill_context 中提取工具参数模板，生成简洁的参数覆盖表。

        Parses lines like:
            [宠物搬运] tool:lalamove_estimate → has_pet=True, vehicle_type=pet_friendly
            [宠物搬运] tool:amap_direction → strategy=shortest_time
        """
        import re
        lines = skill_context.split("\n")
        tool_lines = [l for l in lines if "tool:" in l]

        if not tool_lines:
            return "（无特殊工具参数要求，使用默认值）"

        params_table = "| 工具 | 场景 | 参数覆盖 |\n|------|------|----------|\n"
        for line in tool_lines:
            # Parse: [场景名] tool:tool_name → key=val, key=val
            match = re.match(r'\[(.+?)\]\s*tool:(.+?)\s*→\s*(.+)', line)
            if match:
                scene = match.group(1)
                tool = match.group(2).strip()
                params = match.group(3).strip()
                params_table += f"| `{tool}` | {scene} | `{params}` |\n"

        return params_table

    # ── 工具查找 ────────────────────────────────────────────

    def _find_tool(self, name: str):
        """按名称查找工具实例。"""
        from app.mcp.tool_wrappers import get_tool_by_name
        return get_tool_by_name(name)

    # ── 从工具结果提取（高精度） ────────────────────────────

    @staticmethod
    def _extract_from_tool_results(tool_call_log: list[dict]) -> dict:
        """
        从工具调用的结构化 JSON 结果中直接提取数据。

        比正则解析 LLM 文本更准确，因为工具返回的是确定的 JSON。
        """
        import json as _json
        updates: dict[str, Any] = {}

        for entry in tool_call_log:
            tool = entry.get("tool", "")
            raw = entry.get("result_json", "")

            # 跳过错误结果
            if not raw or raw.startswith('{"error"'):
                continue

            try:
                data = _json.loads(raw)
            except (_json.JSONDecodeError, TypeError):
                continue

            if tool == "amap_direction":
                updates["route"] = {
                    "distance_km": data.get("distance_km", 0),
                    "duration_min": data.get("duration_min", 0),
                    "toll_yuan": data.get("toll_yuan", 0),
                    "restrictions": data.get("restrictions", []),
                }
                # 从 args 提取地址
                args = entry.get("args", {})
                if args.get("origin") and not updates.get("from_address"):
                    updates["from_address"] = args["origin"]
                if args.get("destination") and not updates.get("to_address"):
                    updates["to_address"] = args["destination"]

            elif tool == "lalamove_estimate":
                vehicle_type = data.get("vehicle_type", "")
                capacity = data.get("vehicle_capacity_m3", 0)
                total = data.get("total_estimate_yuan", 0)
                price_range = data.get("price_range_yuan", {})
                updates["vehicle_recommendation"] = {
                    "type": vehicle_type,
                    "capacity_m3": capacity,
                    "pet_friendly": data.get("pet_friendly", False),
                    "capacity_fit": data.get("capacity_fit", True),
                }
                updates["freight_estimate"] = {
                    "total_estimate_yuan": total,
                    "price_range": {
                        "min_yuan": price_range.get("min_yuan", int(total * 0.85)),
                        "max_yuan": price_range.get("max_yuan", int(total * 1.15)),
                    },
                    "uncertainty": data.get("uncertainty_level", "medium"),
                }
                # 从 args 提取体积
                args = entry.get("args", {})
                vol = args.get("total_volume_m3")
                if vol and not updates.get("total_volume_m3"):
                    try:
                        updates["total_volume_m3"] = float(vol)
                    except (ValueError, TypeError):
                        pass

        return updates

    # ── 结构化数据提取（文本兜底） ───────────────────────────

    def _extract_moving_state(self, text: str, existing_ms: dict) -> dict:
        """
        从 Agent 自然语言响应中提取搬家相关的结构化数据。

        使用正则 + 启发式规则，不依赖 JSON 解析（Agent 输出是 Markdown）。
        """
        updates: dict[str, Any] = {}

        # 提取总体积
        vol_match = re.search(r'(?:总)?体积[约]?[:：]?\s*(\d+\.?\d*)\s*m³', text)
        if not vol_match:
            vol_match = re.search(r'(\d+\.?\d*)\s*m³', text)
        if vol_match:
            vol = float(vol_match.group(1))
            if 1 < vol < 100:  # 合理范围
                updates["total_volume_m3"] = vol

        # 提取车型
        vehicle_match = re.search(r'(?:推荐)?车[型类][:：]?\s*(.+?)(?:[，,\n]|$)', text)
        if not vehicle_match:
            vehicle_match = re.search(r'(小面包车|4\.2m|6\.8m|厢式货车|平板车)', text)
        if vehicle_match:
            vtype = vehicle_match.group(1).strip()
            capacity = 20.0  # default
            if "小面包" in vtype:
                capacity = 8.0
            elif "6.8" in vtype:
                capacity = 32.0
            pet_friendly = "宠物" in text and "友好" in text
            updates["vehicle_recommendation"] = {
                "type": vtype,
                "capacity_m3": capacity,
                "pet_friendly": pet_friendly,
                "capacity_fit": updates.get("total_volume_m3", existing_ms.get("total_volume_m3", 99)) <= capacity,
            }

        # 提取运费
        freight_match = re.search(r'(?:运费|费用|总价)[:：]?\s*[¥￥]\s*(\d+[,.\d]*)\s*[-~到]\s*[¥￥]?\s*(\d+[,.\d]*)', text)
        if not freight_match:
            freight_match = re.search(r'[¥￥]\s*(\d+[,.\d]*)\s*[-~到]\s*[¥￥]?\s*(\d+[,.\d]*)', text)
        if freight_match:
            min_yuan = float(freight_match.group(1).replace(",", ""))
            max_yuan = float(freight_match.group(2).replace(",", ""))
            updates["freight_estimate"] = {
                "total_estimate_yuan": round((min_yuan + max_yuan) / 2),
                "price_range": {"min_yuan": min_yuan, "max_yuan": max_yuan},
                "uncertainty": "low" if (max_yuan - min_yuan) / max(max_yuan, 1) < 0.2 else "medium",
            }

        # 提取路线
        dist_match = re.search(r'(?:距离)[:：]?\s*(\d+\.?\d*)\s*(?:公里|km)', text)
        time_match = re.search(r'(?:时间|耗时|时长)[:：]?\s*(\d+\.?\d*)\s*(?:分钟|min)', text)
        if dist_match:
            updates["route"] = {
                "distance_km": float(dist_match.group(1)),
                "duration_min": float(time_match.group(1)) if time_match else 0.0,
                "toll_yuan": 0.0,
                "restrictions": [],
            }

        # 提取地址
        from_match = re.search(r'搬出[:：]?\s*(.+?)(?:[→\-]|搬入)', text)
        to_match = re.search(r'搬入[:：]?\s*(.+?)(?:[，,\n]|$)', text)
        if from_match and not existing_ms.get("from_address"):
            updates["from_address"] = from_match.group(1).strip()
        if to_match and not existing_ms.get("to_address"):
            updates["to_address"] = to_match.group(1).strip()

        # 提取装箱方案
        box_counts: dict[str, int] = {}
        for box_type, pattern in [
            ("small_boxes", r'小[号型]箱?[：:]?\s*(\d+)'),
            ("medium_boxes", r'中[号型]箱?[：:]?\s*(\d+)'),
            ("large_boxes", r'大[号型]箱?[：:]?\s*(\d+)'),
            ("wardrobe_boxes", r'挂衣箱?[：:]?\s*(\d+)'),
        ]:
            m = re.search(pattern, text)
            if m:
                box_counts[box_type] = int(m.group(1))
        if box_counts:
            updates.setdefault("box_plan", {}).update(box_counts)

        return updates

    def _extract_renovation_state(self, text: str, existing_rs: dict) -> dict:
        """从 Agent 响应中提取装修相关的结构化数据。"""
        updates: dict[str, Any] = {}

        # 提取预算
        budget_match = re.search(r'(?:预算|总价)[:：]?\s*[¥￥]?\s*(\d+[,.\d]*)\s*(?:万|元)', text)
        if not budget_match:
            budget_match = re.search(r'(\d+)\s*万', text)
        if budget_match:
            val = float(budget_match.group(1).replace(",", ""))
            if "万" in text[budget_match.start():budget_match.end()]:
                val *= 10000
            if 10000 < val < 10000000:
                updates["total_budget_yuan"] = val

        # 提取工期
        days_match = re.search(r'(?:工期|预计)\s*(\d+)\s*(?:天|日)', text)
        if days_match:
            updates["_total_days"] = int(days_match.group(1))

        # 提取面积
        area_match = re.search(r'(\d+)\s*(?:平|平米|㎡|m2)', text)
        if area_match and not existing_rs.get("house_area_m2"):
            updates["house_area_m2"] = float(area_match.group(1))

        return updates

    # ── 决策溯源 ────────────────────────────────────────────

    def _build_traces(self, tool_call_log: list[dict], moving_updates: dict) -> list[str]:
        """从工具调用日志构建决策溯源摘要。"""
        traces: list[str] = []

        for entry in tool_call_log:
            tool = entry["tool"]
            if tool == "amap_direction":
                traces.append(f"🗺️ 路线规划 → 高德地图工具")
            elif tool == "lalamove_estimate":
                traces.append(f"🚛 运费估算 → 货拉拉工具")
            elif tool == "rag_search":
                args = entry.get("args", {})
                query = args.get("query", "")[:50]
                traces.append(f"📚 知识检索: {query}")

        if moving_updates.get("vehicle_recommendation"):
            v = moving_updates["vehicle_recommendation"]
            traces.append(f"🚛 推荐车型: {v.get('type', '?')} (容量 {v.get('capacity_m3', '?')}m³)")

        if moving_updates.get("freight_estimate"):
            fe = moving_updates["freight_estimate"]
            pr = fe.get("price_range", {})
            traces.append(f"💰 运费: ¥{pr.get('min_yuan', '?')} - ¥{pr.get('max_yuan', '?')}")

        if moving_updates.get("total_volume_m3"):
            traces.append(f"📐 物品总体积: {moving_updates['total_volume_m3']}m³")

        return traces
