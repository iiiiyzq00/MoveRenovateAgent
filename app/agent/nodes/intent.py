"""
意图分类节点 — node_intent_classify。

基于风险 6 优化：单次 LLM 调用同时输出 intent + entities + rag_prejudge。

Usage:
    from app.agent.nodes.intent import IntentClassifyNode
    node = IntentClassifyNode(llm_client)
    result = await node(state)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.llm_client import LLMClient
from app.agent.state import MasterState
from app.config import get_config

logger = logging.getLogger(__name__)

# ── 偏好信号词（风险 7） ──────────────────────────────────
PREFERENCE_SIGNAL_WORDS: list[str] = [
    "我喜欢", "我偏好", "我想要", "我不喜欢", "我讨厌",
    "预算紧", "预算比较紧", "预算宽松", "不差钱", "省钱", "性价比",
    "环保", "甲醛", "无毒", "E0", "E1", "儿童安全", "适老", "无障碍",
    "简约", "北欧", "中式", "日式", "工业风", "现代", "极简",
    "有小孩", "有老人", "有婴儿", "孕妇",
    "比较急", "不着急", "慢慢来",
    "品质", "质量", "耐用", "品牌",
]

# ── Prompt 模板路径 ──────────────────────────────────────
PROMPT_PATH: Path = Path(__file__).resolve().parent.parent / "prompts" / "intent_classify.md"


class IntentClassifyNode:
    """
    意图分类节点 — 合并 RAG 预判（风险 6 优化）。

    单次 LLM 调用完成三项任务：
    1. 意图分类（new_topic / incremental_update / clarification / reset）
    2. 实体提取（MODIFY / APPEND / DELETE + 数值变化判定）
    3. RAG 检索预判（query_type + rag_queries + rag_priority）
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        """
        Args:
            llm_client: LLM 客户端，None 则自动创建
        """
        self.llm = llm_client or LLMClient(temperature=0.1)  # 意图分类用低温，提高稳定性
        self._prompt_template: str | None = None

    def _load_prompt(self) -> str:
        """懒加载 Prompt 模板。"""
        if self._prompt_template is None:
            if PROMPT_PATH.exists():
                self._prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
            else:
                logger.warning(f"Prompt file not found: {PROMPT_PATH}, using inline default")
                self._prompt_template = self._default_prompt()
        return self._prompt_template

    async def __call__(self, state: MasterState) -> dict:
        """
        执行意图分类。

        Input:  MasterState（读取 last_user_input, conversation_round, entity_index）
        Output: dict（更新 intent, parsed_entities, _rag_prejudge, 可能更新 user_profile 偏好信号）

        路由逻辑（在 graph.py 中）：
        - intent=reset → node_session_reset
        - intent=clarification → node_clarify_ask（或直接生成追问）
        - intent=new_topic → node_skill_activate
        - intent=incremental_update → node_cascade_compute
        """
        user_input = state.get("last_user_input", "")
        current_round = state.get("conversation_round", 0)

        # 第 0 轮一定是 new_topic
        if current_round == 0:
            logger.info("[intent] round=0 → force new_topic")
            return self._new_topic_response(user_input, current_round)

        # 从 Redis/内存加载实体索引用于对比
        entity_index = await self._load_entity_index(state.get("session_id", ""))

        # 构建 Prompt
        prompt = self._build_prompt(state)

        # 调用 LLM
        try:
            raw = await self.llm.chat(prompt)
            result = self._parse_result(raw)
        except Exception as e:
            logger.error(f"[intent] LLM call failed: {e}, falling back to heuristic")
            result = self._heuristic_fallback(state)

        intent = result.get("intent", "new_topic")
        entities = result.get("entities", [])

        # 用 entity_index 精确实体比较（增量识别强化）
        entities = self._enrich_entity_actions(entities, entity_index)

        rag_prejudge = result.get("rag_prejudge", {})

        # NO_CHANGE 过滤：语义相同 + 数值 <5%
        entities = self._filter_no_change(entities, state)

        # 自动补充：检测偏好信号词（风险 7）
        preference_signals = self._detect_preference_signals(user_input)

        output: dict[str, Any] = {
            "intent": intent,
            "intent_confidence": result.get("intent_confidence", 0.7),
            "parsed_entities": entities,
            "_rag_prejudge": rag_prejudge,
            "updated_at": datetime.now().isoformat(),
        }

        if preference_signals:
            output["_preference_signals"] = preference_signals

        logger.info(
            f"[intent] round={current_round}, intent={intent}, "
            f"entities={len(entities)}, rag_priority={rag_prejudge.get('rag_priority', 'none')}"
        )

        return output

    # ── Prompt 构建 ──────────────────────────────────────

    def _build_prompt(self, state: MasterState) -> str:
        """构建完整的 LLM Prompt（使用简单替换避免 JSON 大括号冲突）。"""
        template = self._load_prompt()

        # 提取最近 3 轮对话摘要
        prev_inputs = self._get_prev_inputs(state)

        # 提取实体索引
        entity_index = self._build_entity_index(state)

        # 使用 str.replace 代替 str.format，避免 Prompt 中 JSON 示例的
        # 大括号与 format() 语法冲突（如 {{"name": ...}}）
        return (
            template
            .replace("{user_input}", state.get("last_user_input", ""))
            .replace("{prev_inputs}", json.dumps(prev_inputs, ensure_ascii=False))
            .replace("{entity_index}", json.dumps(entity_index, ensure_ascii=False, indent=2))
            .replace("{conversation_round}", str(state.get("conversation_round", 0)))
        )

    def _get_prev_inputs(self, state: MasterState) -> list[str]:
        """从 messages 中提取最近 3 轮用户输入。"""
        messages = state.get("messages", [])
        user_msgs: list[str] = []
        for m in messages:
            if hasattr(m, "type") and m.type == "human":
                user_msgs.append(m.content if hasattr(m, "content") else str(m))
            elif isinstance(m, dict) and m.get("type") == "human":
                user_msgs.append(m.get("content", ""))
        return user_msgs[-3:]

    def _build_entity_index(self, state: MasterState) -> dict[str, Any]:
        """
        从当前 state 构建实体索引摘要。

        只提取高层关键字段，避免 state dump 过大撑爆 context。
        """
        idx: dict[str, Any] = {}

        ms = state.get("moving_state", {})
        if ms.get("from_address"):
            idx["moving_state.from_address"] = ms["from_address"]
        if ms.get("to_address"):
            idx["moving_state.to_address"] = ms["to_address"]
        if ms.get("move_date"):
            idx["moving_state.move_date"] = ms["move_date"]
        if ms.get("total_volume_m3"):
            idx["moving_state.total_volume_m3"] = ms["total_volume_m3"]
        if ms.get("vehicle_recommendation"):
            idx["moving_state.vehicle_recommendation"] = {
                "type": ms["vehicle_recommendation"].get("type"),
                "capacity_m3": ms["vehicle_recommendation"].get("capacity_m3"),
            }
        if ms.get("freight_estimate"):
            fe = ms["freight_estimate"]
            idx["moving_state.freight_estimate"] = {
                "total": fe.get("total_estimate_yuan") or fe.get("total"),
                "range": fe.get("price_range"),
            }

        # 物品摘要（只列名称+数量，不列全部详情）
        inventory = ms.get("inventory", [])
        if inventory:
            idx["moving_state.inventory_summary"] = [
                {"name": i.get("name"), "qty": i.get("quantity", 1)}
                for i in inventory[:20]  # 最多 20 条
            ]
            idx["moving_state.inventory_count"] = len(inventory)

        rs = state.get("renovation_state", {})
        if rs.get("house_area_m2"):
            idx["renovation_state.house_area_m2"] = rs["house_area_m2"]
        if rs.get("layout"):
            idx["renovation_state.layout"] = rs["layout"]
        if rs.get("move_in_condition"):
            idx["renovation_state.move_in_condition"] = rs["move_in_condition"]
        if rs.get("total_budget_yuan"):
            idx["renovation_state.total_budget_yuan"] = rs["total_budget_yuan"]
        if rs.get("budget_allocation"):
            idx["renovation_state.budget_allocation"] = {
                k: v.get("amount") if isinstance(v, dict) else v
                for k, v in rs["budget_allocation"].items()
            }
        if rs.get("style_preference"):
            idx["renovation_state.style_preference"] = rs["style_preference"]

        return idx

    # ── 结果解析 ─────────────────────────────────────────

    def _parse_result(self, raw: str) -> dict[str, Any]:
        """解析 LLM 返回的 JSON。"""
        # 提取 JSON
        json_str = raw.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            json_str = "\n".join(lines)

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # 尝试从文本中提取 { ... } 块
            start = json_str.find("{")
            end = json_str.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(json_str[start:end])
            raise

    def _filter_no_change(
        self, entities: list[dict[str, Any]], state: MasterState
    ) -> list[dict[str, Any]]:
        """
        过滤 NO_CHANGE 实体：语义相同 + 数值 <5%。

        规则（决策 1.4 + 模块 4）：
        - action=NO_CHANGE：直接过滤
        - 数值型 MODIFY 变化 <5%：过滤
        """
        filtered: list[dict[str, Any]] = []
        for e in entities:
            # 语义/规则已标记 NO_CHANGE
            if e.get("action") == "NO_CHANGE":
                logger.info(f"[intent] Entity '{e.get('key')}' NO_CHANGE (semantic={e.get('_semantic_skip')}, small_change={e.get('_small_change_skip')})")
                continue

            # 数值型 MODIFY：检查变化幅度
            if e.get("action") == "MODIFY" and e.get("data_type") == "numeric":
                old = e.get("old_value")
                new = e.get("new_value")
                if old is not None and new is not None:
                    try:
                        old_f, new_f = float(old), float(new)
                        if old_f != 0 and abs(new_f - old_f) / abs(old_f) < 0.05:
                            logger.info(f"[intent] Entity '{e['key']}' {old_f}→{new_f} <5%, treated as NO_CHANGE")
                            continue
                    except (ValueError, TypeError):
                        pass

            filtered.append(e)

        return filtered

    # ── 降级兜底 ─────────────────────────────────────────

    def _heuristic_fallback(self, state: MasterState) -> dict[str, Any]:
        """LLM 调用失败时的启发式分类（纯规则）。"""
        user_input = state.get("last_user_input", "")
        entity_index = self._build_entity_index(state)

        # 检测 reset
        reset_keywords = ["重新", "重来", "从头", "全部重算", "重新规划"]
        if any(kw in user_input for kw in reset_keywords):
            return {
                "intent": "reset",
                "intent_confidence": 0.8,
                "entities": [],
                "rag_prejudge": {"query_type": None, "rag_queries": [], "rag_priority": "none"},
            }

        # 如果 entity_index 非空，默认 incremental_update
        if entity_index:
            return {
                "intent": "incremental_update",
                "intent_confidence": 0.5,
                "entities": [],
                "rag_prejudge": {"query_type": "general_faq", "rag_queries": [user_input], "rag_priority": "optional"},
            }

        # 否则 new_topic
        return {
            "intent": "new_topic",
            "intent_confidence": 0.5,
            "entities": [],
            "rag_prejudge": {"query_type": "general_faq", "rag_queries": [user_input], "rag_priority": "optional"},
        }

    # ── 新会话快速路径 ───────────────────────────────────

    def _new_topic_response(self, user_input: str, current_round: int) -> dict[str, Any]:
        """第 0 轮的快速响应（不调 LLM，省成本）。"""
        # 初步实体提取（关键词规则，为 RAG 预判提供信号）
        rag_prejudge: dict[str, Any] = {
            "query_type": None,
            "rag_queries": [],
            "rag_priority": "none",
        }

        # 简单关键词检测 → RAG 预判
        if any(kw in user_input for kw in ["打包", "怎么搬", "搬运", "箱子", "纸箱"]):
            rag_prejudge = {"query_type": "item_packing", "rag_queries": [user_input], "rag_priority": "required"}
        elif any(kw in user_input for kw in ["预算", "多少钱", "费用", "价格"]):
            rag_prejudge = {"query_type": "cost_reference", "rag_queries": [user_input], "rag_priority": "optional"}
        elif any(kw in user_input for kw in ["规范", "标准", "防水", "电路", "承重", "安全"]):
            rag_prejudge = {"query_type": "safety_code", "rag_queries": [user_input], "rag_priority": "required"}

        return {
            "intent": "new_topic",
            "intent_confidence": 1.0,
            "parsed_entities": [],
            "_rag_prejudge": rag_prejudge,
            "updated_at": datetime.now().isoformat(),
        }

    # ── 实体索引增强 ────────────────────────────────────

    async def _load_entity_index(self, session_id: str) -> dict[str, Any]:
        """从 Redis/内存加载当前会话的实体索引。"""
        try:
            from app.memory.session_store import get_session_store
            store = get_session_store()
            return await store.get_entity_index(session_id)
        except Exception:
            return {}

    def _enrich_entity_actions(
        self, entities: list[dict], entity_index: dict[str, Any]
    ) -> list[dict]:
        """
        用 entity_index 精确判定实体操作类型（模块4增强：语义比较）。

        - 地址/物品名实体：使用 embedding 语义相似度比较（阈值 0.85）
        - 数值实体：使用绝对差值（<5% 忽略）
        - 文本实体：精确比对
        """
        enriched = []
        for e in entities:
            key = e.get("key", "")
            if not key:
                enriched.append(e)
                continue

            existing = entity_index.get(key)
            if existing is not None:
                old_val = existing
                try:
                    old_val = json.loads(existing) if isinstance(existing, str) else existing
                except (json.JSONDecodeError, TypeError):
                    pass

                new_val = e.get("new_value", "")

                # 语义比较：地址/物品名字段
                is_semantic_field = any(kw in key for kw in ["address", "name", "from_", "to_"])
                if is_semantic_field and isinstance(old_val, str) and isinstance(new_val, str):
                    try:
                        from app.rag.embedding import semantic_similarity
                        sim = semantic_similarity(str(old_val), str(new_val))
                        if sim >= 0.85:
                            # 语义相同 → NO_CHANGE
                            logger.info(f"[intent] Semantic match: '{old_val}' ≈ '{new_val}' (sim={sim:.2f}), skipping")
                            e["action"] = "NO_CHANGE"
                            e["_semantic_skip"] = True
                            enriched.append(e)
                            continue
                    except Exception as err:
                        logger.debug(f"Semantic similarity failed: {err}, falling back to exact match")

                # 数值比较：变化 <5% 忽略
                if e.get("data_type") == "numeric":
                    try:
                        old_f, new_f = float(old_val), float(new_val)
                        if old_f != 0 and abs(new_f - old_f) / abs(old_f) < 0.05:
                            logger.info(f"[intent] Small change: '{key}' {old_f}→{new_f} <5%, skipping")
                            e["action"] = "NO_CHANGE"
                            e["_small_change_skip"] = True
                            enriched.append(e)
                            continue
                    except (ValueError, TypeError):
                        pass

                e["action"] = "MODIFY"
                e["old_value"] = old_val
                e["_matched_from_index"] = True
            else:
                e["action"] = "APPEND"
                e["_matched_from_index"] = False

            enriched.append(e)

        return enriched

    # ── 偏好信号检测（风险 7） ────────────────────────────

    def _detect_preference_signals(self, user_input: str) -> list[dict[str, Any]]:
        """检测用户输入中的偏好信号词。"""
        signals: list[dict[str, Any]] = []
        for kw in PREFERENCE_SIGNAL_WORDS:
            if kw in user_input:
                signals.append({
                    "keyword": kw,
                    "source": "keyword_match",
                    "detected_at": datetime.now().isoformat(),
                })

        # 正则：检测预算约束词
        import re
        constraint_patterns = [
            (r"不能超过(\d+)万", "budget_sensitivity", "tight"),
            (r"最多.{0,3}(\d+)万", "budget_sensitivity", "tight"),
            (r"控制在(\d+)万", "budget_sensitivity", "tight"),
            (r"(\d+)万.{0,3}以内", "budget_sensitivity", "tight"),
        ]
        for pattern, key, value in constraint_patterns:
            if re.search(pattern, user_input):
                signals.append({
                    "keyword": f"regex:{pattern}",
                    "preference_key": key,
                    "preference_value": value,
                    "source": "regex_match",
                    "detected_at": datetime.now().isoformat(),
                })
                break  # 一个约束词只触发一次

        return signals

    @staticmethod
    def _default_prompt() -> str:
        """内置默认 Prompt（当外部模板文件不可用时）。"""
        return """你是智能搬装规划系统的意图分析器。根据用户当前输入和会话状态，一次性完成三件事。

用户当前输入: {user_input}
对话历史: {prev_inputs}
当前方案实体: {entity_index}
会话轮次: {conversation_round}

请输出 JSON（只输出 JSON，不要其他文字）:
{{
  "intent": "new_topic|incremental_update|clarification|reset",
  "intent_confidence": 0.0-1.0,
  "entities": [
    {{"key": "...", "action": "MODIFY|APPEND|DELETE", "old_value": null, "new_value": "...", "data_type": "numeric|text|boolean|array"}}
  ],
  "rag_prejudge": {{
    "query_type": "item_packing|construction_standard|safety_code|space_dimension|quantity_estimation|cost_reference|general_faq|null",
    "rag_queries": [],
    "rag_priority": "required|optional|none"
  }},
  "needs_clarification": false,
  "clarification_question": null
}}"""
