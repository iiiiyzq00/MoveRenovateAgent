"""
情景记忆 — 历史方案存储与复用。

逐字稿第三块：「存他历史上生成过的方案，支持下次直接在上次基础上改」

提供：
- 方案自动保存（每次对话完成后）
- 跨会话方案加载（老用户回来时自动加载最近方案）
- 方案列表/恢复 API

Usage:
    from app.memory.plan_store import save_plan, load_latest_plan, list_user_plans
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 保存方案
# ═══════════════════════════════════════════════════════════════

async def save_plan(
    user_id: str,
    state: dict,
    *,
    session_id: str = "",
    tokens_used: int | None = None,
    tools_called: list[str] | None = None,
) -> str | None:
    """
    保存当前方案到 historical_plans 表。

    提取 moving_state + renovation_state 作为 plan_snapshot，
    自动生成文本摘要供列表展示。

    Args:
        user_id: 用户 ID
        state: MasterState（graph 执行后的完整状态）
        session_id: 会话 ID（用于追踪）
        tokens_used: 可选的 token 消耗
        tools_called: 可选的工具调用列表

    Returns:
        plan_id (UUID 字符串)，失败返回 None
    """
    if not user_id or user_id == "anonymous":
        return None

    ms = state.get("moving_state", {})
    rs = state.get("renovation_state", {})

    # 如果没有任何实质性内容，跳过保存
    if not ms.get("from_address") and not ms.get("total_volume_m3") and not rs.get("total_budget_yuan"):
        return None

    # 构建方案快照
    snapshot: dict[str, Any] = {
        "moving_state": {
            "from_address": ms.get("from_address"),
            "to_address": ms.get("to_address"),
            "move_date": ms.get("move_date"),
            "inventory": ms.get("inventory", []),
            "total_volume_m3": ms.get("total_volume_m3"),
            "total_weight_kg": ms.get("total_weight_kg"),
            "box_plan": ms.get("box_plan", {}),
            "vehicle_recommendation": ms.get("vehicle_recommendation"),
            "freight_estimate": ms.get("freight_estimate"),
            "route": ms.get("route"),
            "packing_sequence": ms.get("packing_sequence"),
            "last_updated_round": ms.get("last_updated_round", 0),
        },
        "renovation_state": {
            "house_area_m2": rs.get("house_area_m2"),
            "layout": rs.get("layout"),
            "move_in_condition": rs.get("move_in_condition"),
            "total_budget_yuan": rs.get("total_budget_yuan"),
            "budget_allocation": rs.get("budget_allocation"),
            "construction_phases": rs.get("construction_phases"),
            "materials": rs.get("materials"),
            "style_preference": rs.get("style_preference"),
            "special_needs": rs.get("special_needs"),
            "last_updated_round": rs.get("last_updated_round", 0),
        },
        "active_skills": state.get("active_skills", []),
        "conversation_round": state.get("conversation_round", 0),
    }

    # 生成摘要
    summary = _build_summary(snapshot)

    # 计算版本号（同一用户方案数 + 1）
    try:
        import asyncpg
        from app.config import get_config
        cfg = get_config()
        conn = await asyncpg.connect(cfg.postgres_dsn, timeout=3)
        try:
            # 获取当前版本号
            row = await conn.fetchrow(
                "SELECT COALESCE(MAX(plan_version), 0) + 1 AS next_version "
                "FROM historical_plans WHERE user_id = $1::uuid",
                user_id,
            )
            version = row["next_version"] if row else 1

            # 插入新方案
            plan_id_row = await conn.fetchrow(
                """INSERT INTO historical_plans
                   (user_id, plan_version, plan_snapshot, summary, tokens_used, tools_called)
                   VALUES ($1::uuid, $2, $3::jsonb, $4, $5, $6)
                   RETURNING id""",
                user_id,
                version,
                json.dumps(snapshot, ensure_ascii=False, default=str),
                summary,
                tokens_used,
                tools_called or [],
            )
            plan_id = str(plan_id_row["id"]) if plan_id_row else None

            # 保持每个用户最多 10 份方案（删除最旧的）
            await conn.execute(
                """DELETE FROM historical_plans
                   WHERE user_id = $1::uuid AND id NOT IN (
                       SELECT id FROM historical_plans
                       WHERE user_id = $1::uuid
                       ORDER BY created_at DESC LIMIT 10
                   )""",
                user_id,
            )

            if plan_id:
                logger.info(f"[plan_store] Saved plan {plan_id} v{version} for user={user_id}")
            return plan_id

        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"[plan_store] Save failed (non-critical): {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# 加载方案
# ═══════════════════════════════════════════════════════════════

async def load_latest_plan(user_id: str) -> dict | None:
    """
    加载用户最近一次方案。

    Args:
        user_id: 用户 ID

    Returns:
        {
            "plan_id": str,
            "plan_version": int,
            "created_at": str,
            "summary": str,
            "moving_state": dict,    # 可直接注入 MasterState
            "renovation_state": dict,
            "active_skills": list,
        }
        若无历史方案返回 None
    """
    if not user_id or user_id == "anonymous":
        return None

    try:
        import asyncpg
        from app.config import get_config
        cfg = get_config()
        conn = await asyncpg.connect(cfg.postgres_dsn, timeout=3)
        try:
            row = await conn.fetchrow(
                """SELECT id, plan_version, plan_snapshot, summary, created_at
                   FROM historical_plans
                   WHERE user_id = $1::uuid
                   ORDER BY created_at DESC LIMIT 1""",
                user_id,
            )
            if not row:
                return None

            snapshot = row["plan_snapshot"]
            if isinstance(snapshot, str):
                snapshot = json.loads(snapshot)

            result = {
                "plan_id": str(row["id"]),
                "plan_version": row["plan_version"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                "summary": row["summary"] or "",
                "moving_state": snapshot.get("moving_state", {}),
                "renovation_state": snapshot.get("renovation_state", {}),
                "active_skills": snapshot.get("active_skills", []),
            }

            logger.info(f"[plan_store] Loaded plan {result['plan_id']} v{result['plan_version']} for user={user_id}")
            return result

        finally:
            await conn.close()
    except Exception as e:
        logger.debug(f"[plan_store] Load failed (non-critical): {e}")
        return None


async def get_plan(plan_id: str) -> dict | None:
    """获取指定方案的完整快照。"""
    try:
        import asyncpg
        from app.config import get_config
        cfg = get_config()
        conn = await asyncpg.connect(cfg.postgres_dsn, timeout=3)
        try:
            row = await conn.fetchrow(
                """SELECT id, user_id, plan_version, plan_snapshot, summary, tokens_used, tools_called, created_at
                   FROM historical_plans WHERE id = $1::uuid""",
                plan_id,
            )
            if not row:
                return None

            snapshot = row["plan_snapshot"]
            if isinstance(snapshot, str):
                snapshot = json.loads(snapshot)

            return {
                "plan_id": str(row["id"]),
                "user_id": str(row["user_id"]),
                "plan_version": row["plan_version"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                "summary": row["summary"] or "",
                "tokens_used": row["tokens_used"],
                "tools_called": row["tools_called"] or [],
                "snapshot": snapshot,
            }
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"[plan_store] Get plan failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# 列表查询
# ═══════════════════════════════════════════════════════════════

async def list_user_plans(user_id: str, limit: int = 5) -> list[dict]:
    """
    列出用户历史方案摘要。

    Returns:
        [{plan_id, plan_version, summary, created_at, key_info}]
    """
    if not user_id or user_id == "anonymous":
        return []

    try:
        import asyncpg
        from app.config import get_config
        cfg = get_config()
        conn = await asyncpg.connect(cfg.postgres_dsn, timeout=3)
        try:
            rows = await conn.fetch(
                """SELECT id, plan_version, plan_snapshot, summary, created_at
                   FROM historical_plans
                   WHERE user_id = $1::uuid
                   ORDER BY created_at DESC LIMIT $2""",
                user_id, limit,
            )

            plans = []
            for row in rows:
                snapshot = row["plan_snapshot"]
                if isinstance(snapshot, str):
                    snapshot = json.loads(snapshot)

                ms = snapshot.get("moving_state", {})
                rs = snapshot.get("renovation_state", {})

                plans.append({
                    "plan_id": str(row["id"]),
                    "plan_version": row["plan_version"],
                    "summary": row["summary"] or "",
                    "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                    "key_info": {
                        "from": ms.get("from_address"),
                        "to": ms.get("to_address"),
                        "volume_m3": ms.get("total_volume_m3"),
                        "vehicle": ms.get("vehicle_recommendation", {}).get("type") if ms.get("vehicle_recommendation") else None,
                        "budget_yuan": rs.get("total_budget_yuan"),
                        "style": rs.get("style_preference"),
                    },
                })

            return plans
        finally:
            await conn.close()
    except Exception as e:
        logger.debug(f"[plan_store] List failed (non-critical): {e}")
        return []


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _build_summary(snapshot: dict) -> str:
    """从方案快照生成人类可读的文本摘要。"""
    ms = snapshot.get("moving_state", {})
    rs = snapshot.get("renovation_state", {})

    parts = []

    from_addr = ms.get("from_address", "")
    to_addr = ms.get("to_address", "")
    if from_addr and to_addr:
        parts.append(f"搬家: {from_addr} → {to_addr}")

    volume = ms.get("total_volume_m3")
    if volume:
        vehicle = ms.get("vehicle_recommendation", {}).get("type", "")
        parts.append(f"体积 {volume}m³" + (f" ({vehicle})" if vehicle else ""))

    freight = ms.get("freight_estimate", {})
    if freight:
        pr = freight.get("price_range", {})
        if pr:
            parts.append(f"运费 ¥{pr.get('min_yuan', 0)}-{pr.get('max_yuan', 0)}")

    budget = rs.get("total_budget_yuan")
    if budget:
        parts.append(f"装修 ¥{budget:,.0f}")

    style = rs.get("style_preference")
    if style:
        parts.append(f"风格: {style}")

    layout = rs.get("layout") or ms.get("layout")
    area = rs.get("house_area_m2")
    if layout and area:
        parts.append(f"{layout} {area}m²")

    return " | ".join(parts) if parts else "空方案"
