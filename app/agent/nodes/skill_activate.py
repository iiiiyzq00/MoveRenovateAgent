"""
Skill 激活节点 — node_skill_activate。

关键词 → 倒排索引 O(1) 匹配 → 版本化加载 → 上下文注入。

Usage:
    from app.agent.nodes.skill_activate import SkillActivateNode
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.agent.state import MasterState
from app.skill.registry import get_skill_registry, SkillRegistry

logger = logging.getLogger(__name__)


class SkillActivateNode:
    """
    Skill 语义匹配激活节点。

    流程:
    1. 从 registry 获取短描述 + 关键词倒排索引
    2. 关键词匹配 → 直接激活（confidence ≥ 0.80）
    3. 静默激活（决策 3.3：≥0.65 直接加载，输出标注置信度）
    4. 多 Skill 并行加载（决策 3.1：并行合并 SOP）
    5. 输出 active_skills[] + skill_context（合并约束+工具参数）
    """

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self._registry = registry

    async def _get_registry(self) -> SkillRegistry:
        if self._registry is None:
            self._registry = await get_skill_registry()
        return self._registry

    async def __call__(self, state: MasterState) -> dict:
        """从用户输入激活 Skill（关键词 O(1) + 语义匹配双路径）。"""
        registry = await self._get_registry()
        user_input = state.get("last_user_input", "")

        # ── 路径 1: 关键词匹配（倒排索引 O(1)） ──
        keyword_index = registry.get_keyword_index()
        activated: dict[str, float] = {}  # skill_id → confidence

        for keyword, skill_ids in keyword_index.items():
            if keyword in user_input:
                # 检查负例
                for sid in skill_ids:
                    sop = registry.get_sop(sid)
                    if sop:
                        neg_hit = any(nk in user_input for nk in sop.trigger.get("negative_examples", []))
                        if not neg_hit:
                            activated[sid] = max(activated.get(sid, 0), 0.85)

        # ── 路径 2: 语义匹配（仅在关键词完全未命中时启用，渐进式披露兜底） ──
        keyword_activated = set(activated.keys())
        if not keyword_activated:
            # 关键词 0 命中 → 语义兜底
            semantic_matches = await self._semantic_match(user_input, registry, keyword_activated)
            for sid, conf in semantic_matches.items():
                activated[sid] = conf

        # 去重合并
        final_ids = [sid for sid, conf in activated.items()
                     if conf >= 0.55]  # 最低阈值

        if not final_ids:
            logger.debug("[skill] No skills activated")
            return {
                "active_skills": [],
                "skill_context": None,
                "skill_context_versions": {},
            }

        # 加载完整 SOP + 合并（P0 简化：只取约束和工具参数）
        context_lines: list[str] = []
        versions: dict[str, str] = {}

        for sid in final_ids:
            sop = registry.get_sop(sid)
            if not sop:
                continue
            versions[sid] = sop.version

            # 约束规则
            for c in sop.constraints:
                sev = c.get("severity", "warning")
                emoji = {"mandatory": "🔴", "warning": "🟡", "conditional": "🔵"}.get(sev, "⚪")
                context_lines.append(f"[{sop.display_name}] {emoji} {c['rule']}")

            # 工具参数模板
            for t in sop.tools:
                params_str = ", ".join(f"{k}={v}" for k, v in t.get("params_template", {}).items())
                context_lines.append(f"[{sop.display_name}] tool:{t['tool_name']} → {params_str}")

            # 输出要求
            for item in sop.output.get("must_include", [])[:3]:
                context_lines.append(f"[{sop.display_name}] 必须包含: {item}")

        skill_context = "\n".join(context_lines) if context_lines else None

        logger.info(f"[skill] Activated: {final_ids} (confidence: {activated})")

        return {
            "active_skills": final_ids,
            "skill_context": skill_context,
            "skill_context_versions": versions,
            "updated_at": datetime.now().isoformat(),
        }

    # ── 语义匹配（embedding 相似度） ──────────────────────────

    async def _semantic_match(
        self,
        user_input: str,
        registry: "SkillRegistry",
        skip_ids: set[str] | None = None,
    ) -> dict[str, float]:
        """
        使用 embedding 做语义相似度匹配，捕获关键词遗漏的 Skill。

        仅在关键词未命中任何 Skill 时才启用语义匹配（渐进式披露原则：
        关键词匹配已覆盖明确场景，语义匹配用于模糊场景兜底）。

        Args:
            user_input: 用户输入文本
            registry: SkillRegistry 实例
            skip_ids: 已通过关键词激活的 skill_id 集合（跳过）

        Returns:
            {skill_id: confidence}
        """
        skip = skip_ids or set()
        matches: dict[str, float] = {}

        all_descs = registry.get_short_descs()
        if not all_descs:
            return matches

        # 构建候选列表（排除已激活 + 检查负例 + 无触发示例的）
        candidates: list[tuple[str, str, list[str], float]] = []
        for desc in all_descs:
            if desc.skill_id in skip:
                continue
            sop = registry.get_sop(desc.skill_id)
            if not sop:
                continue

            # 检查负例（语义匹配也要尊重 negative_examples）
            neg_examples = sop.trigger.get("negative_examples", [])
            if any(nk in user_input for nk in neg_examples):
                logger.debug(f"[skill] Semantic skip '{desc.skill_id}': negative match")
                continue

            examples = sop.trigger.get("trigger_examples", [])
            threshold = sop.trigger.get("embedding_threshold", 0.65)
            if examples:
                candidates.append((desc.skill_id, desc.summary, examples, threshold))

        if not candidates:
            return matches

        # 逐个计算语义相似度
        for skill_id, summary, examples, threshold in candidates:
            best_sim = 0.0
            for example in examples:
                try:
                    from app.rag.embedding import semantic_similarity
                    sim = semantic_similarity(user_input, example)
                    if sim > best_sim:
                        best_sim = sim
                except Exception as e:
                    logger.debug(f"[skill] Semantic similarity failed for '{skill_id}': {e}")
                    continue

            if best_sim >= threshold:
                # 映射相似度到置信度: [threshold, 1.0] → [0.55, 0.85]
                conf = 0.55 + (best_sim - threshold) / (1.0 - threshold) * 0.30
                conf = round(min(conf, 0.85), 2)
                matches[skill_id] = conf
                logger.info(
                    f"[skill] Semantic match: '{skill_id}' sim={best_sim:.2f} "
                    f"(threshold={threshold}) → conf={conf}"
                )

        return matches
