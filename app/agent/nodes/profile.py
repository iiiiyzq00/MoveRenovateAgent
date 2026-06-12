"""
画像提炼节点 — node_profile_extract。

每轮对话后调用 LLM 判断是否有值得记忆的偏好。

Usage:
    from app.agent.nodes.profile import ProfileExtractNode
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from app.agent.llm_client import LLMClient
from app.agent.state import MasterState

logger = logging.getLogger(__name__)

# 偏好信号词
PREFERENCE_SIGNALS = [
    "我喜欢", "我偏好", "我不喜欢",
    "预算紧", "不差钱", "省钱", "性价比", "品质优先",
    "环保", "甲醛", "无毒", "E0", "E1", "儿童安全", "适老",
    "简约", "北欧", "中式", "日式", "工业风", "现代", "极简",
    "有小孩", "有老人", "有婴儿", "孕妇",
    "比较急", "不着急", "慢慢来",
]


class ProfileExtractNode:
    """画像提炼节点。"""

    PROMPT = """你是用户画像分析器。从以下对话中提取值得长期记忆的偏好。

## 值得记忆的信息
- 事实：家庭结构（几口人、是否有老人/儿童/宠物）
- 偏好：风格偏好、预算态度（紧/平衡/宽松）、环保优先级
- 约束：时间压力、不可妥协的要求

## 不应记忆的信息
- 一次性的搬家地址、具体预算数字（每次都可能不同）
- 闲聊内容

## 当前画像
{current_profile}

## 对话内容
用户: {user_input}

## 输出 JSON
{{
  "extractions": [
    {{"key": "budget_sensitivity", "value": "tight|balanced|flexible", "confidence": 0.8, "evidence": "用户说..."}}
  ],
  "has_new_info": true|false
}}

只输出 JSON，不要其他文字。
"""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client

    async def __call__(self, state: MasterState) -> dict:
        user_input = state.get("last_user_input", "")
        profile = state.get("user_profile") or {}

        # 检查信号词
        has_signal = any(kw in user_input for kw in PREFERENCE_SIGNALS)
        has_constraint = any(kw in user_input for kw in ["不能超过", "必须", "一定", "千万", "至少"])

        if not has_signal and not has_constraint:
            return {}

        # 规则引擎快速提取
        rule_extractions = self._rule_extract(user_input, state)

        # LLM 补充分类
        llm_extractions = []
        if self.llm and (has_signal or has_constraint):
            try:
                prompt = self.PROMPT.format(
                    current_profile=json.dumps(profile, ensure_ascii=False),
                    user_input=user_input,
                )
                raw = await self.llm.chat(prompt)
                result = self._parse_json(raw)
                llm_extractions = result.get("extractions", [])
            except Exception as e:
                logger.debug(f"Profile LLM extract failed: {e}")

        # 合并（规则优先）
        all_extractions = {e["key"]: e for e in rule_extractions}
        for e in llm_extractions:
            key = e["key"]
            if key not in all_extractions or e.get("confidence", 0) > all_extractions[key].get("confidence", 0):
                all_extractions[key] = e

        if not all_extractions:
            return {}

        # 更新画像
        updated = dict(profile)
        for key, ext in all_extractions.items():
            old = updated.get(key, {})
            new_conf = ext.get("confidence", 0.7)

            if isinstance(old, dict) and old.get("value") == ext["value"]:
                # 相同值 → 提升置信度
                updated[key] = {**old, "confidence": min(old.get("confidence", 0) + 0.05, 0.98),
                                "updated_at": datetime.now().isoformat()}
            elif isinstance(old, dict) and old.get("value") != ext["value"]:
                # 冲突 → 置信度加权
                old_conf = old.get("confidence", 0.5)
                if new_conf > old_conf + 0.1:
                    updated[key] = {"value": ext["value"], "confidence": new_conf,
                                    "previous": old.get("value"),
                                    "updated_at": datetime.now().isoformat()}
            else:
                updated[key] = {"value": ext["value"], "confidence": new_conf,
                                "evidence": ext.get("evidence", ""),
                                "updated_at": datetime.now().isoformat()}

        logger.info(f"[profile] Extracted {len(all_extractions)} preferences: {list(all_extractions.keys())}")

        # 异步写入 PostgreSQL（模块 3）
        import asyncio
        user_id = state.get("user_id", "")
        if user_id and user_id != "anonymous":
            asyncio.create_task(save_user_profile(user_id, updated))

        return {"user_profile": updated}

    def _rule_extract(self, user_input: str, state: MasterState) -> list[dict]:
        """规则引擎快速提取。"""
        extractions = []

        # 预算敏感度
        if any(kw in user_input for kw in ["预算紧", "省钱", "性价比", "便宜"]):
            extractions.append({"key": "budget_sensitivity", "value": "tight", "confidence": 0.82, "evidence": user_input[:60]})
        if any(kw in user_input for kw in ["不差钱", "品质优先", "好一点"]):
            extractions.append({"key": "budget_sensitivity", "value": "flexible", "confidence": 0.82, "evidence": user_input[:60]})
        if re.search(r"不能超过|控制在|最多.{0,3}\d+万", user_input):
            extractions.append({"key": "budget_sensitivity", "value": "tight", "confidence": 0.80, "evidence": "预算约束"})

        # 环保
        if any(kw in user_input for kw in ["环保", "甲醛", "无毒", "E0", "E1"]):
            extractions.append({"key": "eco_priority", "value": True, "confidence": 0.88, "evidence": user_input[:60]})

        # 风格
        for style in ["简约", "北欧", "中式", "日式", "工业风", "现代", "极简", "美式", "欧式", "轻奢"]:
            if style in user_input:
                extractions.append({"key": "style", "value": style, "confidence": 0.85, "evidence": user_input[:60]})
                break

        # 家庭结构
        if any(kw in user_input for kw in ["有小孩", "宝宝", "婴儿", "儿童"]):
            extractions.append({"key": "family_structure", "value": {"has_child": True}, "confidence": 0.85, "evidence": user_input[:60]})
        if any(kw in user_input for kw in ["有老人", "父母", "年迈", "适老"]):
            extractions.append({"key": "family_structure", "value": {"has_elderly": True}, "confidence": 0.85, "evidence": user_input[:60]})
        if any(kw in user_input for kw in ["猫", "狗", "宠物"]):
            extractions.append({"key": "family_structure", "value": {"has_pet": True}, "confidence": 0.90, "evidence": user_input[:60]})

        # 时间压力
        if any(kw in user_input for kw in ["急", "尽快", "越快越好", "赶时间"]):
            extractions.append({"key": "timeline_pressure", "value": "urgent", "confidence": 0.82, "evidence": user_input[:60]})
        if any(kw in user_input for kw in ["不着急", "慢慢来"]):
            extractions.append({"key": "timeline_pressure", "value": "relaxed", "confidence": 0.82, "evidence": user_input[:60]})

        return extractions

    @staticmethod
    def _parse_json(raw: str) -> dict:
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[0].startswith("```"): lines = lines[1:]
            if lines and lines[-1].strip() == "```": lines = lines[:-1]
            raw = "\n".join(lines)
        return json.loads(raw) if raw else {}


# ── PostgreSQL 画像存取（模块 3：跨会话画像加载） ──

async def load_user_profile(user_id: str) -> dict:
    """从 PostgreSQL 加载用户画像。"""
    if not user_id or user_id == "anonymous":
        return {}
    try:
        import asyncpg
        from app.config import get_config
        cfg = get_config()
        conn = await asyncpg.connect(cfg.postgres_dsn, timeout=3)
        try:
            row = await conn.fetchrow(
                "SELECT preferences FROM user_profile WHERE user_id = $1::uuid",
                user_id,
            )
            if row:
                prefs = row["preferences"]
                return prefs if isinstance(prefs, dict) else json.loads(prefs)
        finally:
            await conn.close()
    except Exception as e:
        logger.debug(f"Profile DB load failed (non-critical): {e}")
        return {}


async def save_user_profile(user_id: str, preferences: dict) -> None:
    """保存用户画像到 PostgreSQL。"""
    if not user_id or user_id == "anonymous" or not preferences:
        return
    try:
        import asyncpg
        from app.config import get_config
        cfg = get_config()
        conn = await asyncpg.connect(cfg.postgres_dsn, timeout=3)
        try:
            await conn.execute(
                """INSERT INTO user_profile (user_id, preferences, last_updated)
                   VALUES ($1::uuid, $2::jsonb, NOW())
                   ON CONFLICT (user_id) DO UPDATE SET preferences = $2::jsonb, last_updated = NOW()""",
                user_id, json.dumps(preferences, ensure_ascii=False),
            )
        finally:
            await conn.close()
        logger.info(f"[profile] Saved profile for user={user_id}: {list(preferences.keys())}")
    except Exception as e:
        logger.warning(f"[profile] DB save failed (non-critical): {e}")
        return  # PG 写入失败则跳过 ChromaDB

    # 画像双写：语义文本 → embedding → ChromaDB（向量库）
    try:
        profile_text = _profile_to_text(preferences)
        await _embed_profile_to_chromadb(user_id, profile_text)
    except Exception as e:
        logger.debug(f"[profile] ChromaDB embed skipped (non-critical): {e}")


# ═══════════════════════════════════════════════════════════════
# 画像语义 → 文本描述
# ═══════════════════════════════════════════════════════════════

def _profile_to_text(preferences: dict) -> str:
    """
    将结构化画像 dict 转为自然语言描述，用于 embedding。

    例如:
        {"budget_sensitivity": {"value": "tight"}, "style": {"value": "北欧"}}
        → "用户偏好: 预算敏感度=紧, 风格=北欧"
    """
    parts: list[str] = []

    # 预算敏感度
    budget = preferences.get("budget_sensitivity", {})
    if isinstance(budget, dict) and budget.get("value"):
        label = {"tight": "预算紧张", "balanced": "预算适中", "flexible": "预算充裕"}.get(
            budget["value"], budget["value"]
        )
        parts.append(f"预算={label}")

    # 风格偏好
    style = preferences.get("style", {})
    if isinstance(style, dict) and style.get("value"):
        parts.append(f"风格={style['value']}")

    # 环保优先级
    eco = preferences.get("eco_priority", {})
    if isinstance(eco, dict) and eco.get("value"):
        parts.append("环保优先级=高")

    # 家庭结构
    family = preferences.get("family_structure", {})
    if isinstance(family, dict) and family.get("value"):
        fv = family["value"]
        members: list[str] = []
        if isinstance(fv, dict):
            if fv.get("has_child"):
                members.append("有小孩")
            if fv.get("has_elderly"):
                members.append("有老人")
            if fv.get("has_pet"):
                members.append("有宠物")
        if members:
            parts.append(f"家庭={', '.join(members)}")

    # 时间压力
    timeline = preferences.get("timeline_pressure", {})
    if isinstance(timeline, dict) and timeline.get("value"):
        label = {"urgent": "时间紧迫", "relaxed": "时间充裕"}.get(
            timeline["value"], timeline["value"]
        )
        parts.append(f"时间={label}")

    # 特殊需求
    special = preferences.get("special_needs", [])
    if special:
        parts.append(f"特殊需求={', '.join(special)}")

    if not parts:
        return ""

    return "用户偏好: " + "; ".join(parts)


# ═══════════════════════════════════════════════════════════════
# 画像 → ChromaDB 向量存储
# ═══════════════════════════════════════════════════════════════

async def _embed_profile_to_chromadb(user_id: str, profile_text: str) -> None:
    """
    将画像自然语言描述 embedding 后写入 ChromaDB。

    文档以 user_id 为唯一标识，每次更新覆盖旧版本。
    这样 ChromaDB 中始终保持每个用户的最新画像向量。

    Args:
        user_id: 用户 ID
        profile_text: 画像的自然语言描述（由 _profile_to_text 生成）
    """
    if not profile_text or not profile_text.strip():
        return

    from app.rag.chroma_client import KnowledgeBaseManager
    from app.rag.embedding import get_embedding_function

    manager = KnowledgeBaseManager()
    collection = manager.get_or_create_collection()
    ef = get_embedding_function()

    # 生成 embedding
    embeddings = ef([profile_text])

    # 构建 metadata
    metadata = {
        "source": "user_profile",
        "authority_level": "user_profile",
        "category": "user_profile",
        "subcategory": "用户画像",
        "tags": ["用户画像", "偏好", "跨会话"],
        "user_id": user_id,
    }

    doc_id = f"profile_{user_id}"

    # Upsert: 先删后插（ChromaDB 的 update 对 embedding 支持有限）
    try:
        existing = collection.get(ids=[doc_id])
        if existing and existing.get("ids"):
            collection.update(ids=[doc_id], documents=[profile_text], metadatas=[metadata])
            # 更新 embedding 需要 delete + add
            collection.delete(ids=[doc_id])
    except Exception:
        pass

    collection.add(
        documents=[profile_text],
        metadatas=[metadata],
        ids=[doc_id],
        embeddings=embeddings,
    )

    logger.info(f"[profile] Embedded to ChromaDB for user={user_id}: {profile_text[:80]}")
