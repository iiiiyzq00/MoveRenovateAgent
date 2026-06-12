"""
会话 API — GET /session/{id}/state, POST /session/{id}/undo, /plans。
"""

from fastapi import APIRouter, HTTPException, Query

from app.memory.session_store import get_session_store

router = APIRouter(tags=["Session"])


@router.get("/session/{session_id}/state")
async def get_session_state(session_id: str):
    """获取当前 LangGraph 状态快照。"""
    store = get_session_store()
    state = await store.load_state(session_id)

    if state is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    # 只返回关键字段（不返回完整 messages）
    return {
        "session_id": state.get("session_id"),
        "user_id": state.get("user_id"),
        "conversation_round": state.get("conversation_round"),
        "intent": state.get("intent"),
        "active_skills": state.get("active_skills", []),
        "moving_summary": {
            "from": state.get("moving_state", {}).get("from_address"),
            "to": state.get("moving_state", {}).get("to_address"),
            "volume": state.get("moving_state", {}).get("total_volume_m3"),
            "vehicle": (state.get("moving_state", {}).get("vehicle_recommendation") or {}).get("type"),
        },
        "renovation_summary": {
            "budget": state.get("renovation_state", {}).get("total_budget_yuan"),
            "style": state.get("renovation_state", {}).get("style_preference"),
        },
        "updated_at": state.get("updated_at"),
    }


@router.post("/session/{session_id}/undo")
async def undo_session(session_id: str):
    """撤销上一步操作（从快照栈恢复）。"""
    store = get_session_store()
    snapshot = await store.pop_snapshot(session_id)

    if snapshot is None:
        return {"success": False, "message": "没有可撤销的操作"}

    # 更新实体索引
    ms = snapshot.get("moving_state", {})
    rs = snapshot.get("renovation_state", {})
    entities = {}
    if ms.get("from_address"):
        entities["moving_state.from_address"] = ms["from_address"]
    if ms.get("to_address"):
        entities["moving_state.to_address"] = ms["to_address"]
    if rs.get("total_budget_yuan"):
        entities["renovation_state.total_budget_yuan"] = str(rs["total_budget_yuan"])
    await store.update_entity_index(session_id, entities)

    return {
        "success": True,
        "message": f"已撤销到上一版方案",
        "state_summary": {
            "conversation_round": snapshot.get("conversation_round"),
            "moving_volume": ms.get("total_volume_m3"),
            "renovation_budget": rs.get("total_budget_yuan"),
        },
    }


# ═══════════════════════════════════════════════════════════════
# 历史方案 API（情景记忆）
# ═══════════════════════════════════════════════════════════════

@router.get("/plans")
async def list_plans(user_id: str = Query(..., description="用户 ID")):
    """列出用户历史方案摘要。"""
    from app.memory.plan_store import list_user_plans
    plans = await list_user_plans(user_id, limit=10)
    return {"success": True, "user_id": user_id, "total": len(plans), "plans": plans}


@router.get("/plans/{plan_id}")
async def get_plan_detail(plan_id: str):
    """获取指定方案的完整快照。"""
    from app.memory.plan_store import get_plan
    plan = await get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"success": True, "plan": plan}


@router.post("/plans/{plan_id}/restore")
async def restore_plan(plan_id: str):
    """
    恢复方案到新会话。

    返回方案快照中的 moving_state + renovation_state，
    前端可用此数据开启新会话并注入初始状态。
    """
    from app.memory.plan_store import get_plan
    plan = await get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    snapshot = plan.get("snapshot", {})
    return {
        "success": True,
        "message": f"方案 v{plan['plan_version']} 已恢复 ({plan['summary']})",
        "plan_id": plan_id,
        "moving_state": snapshot.get("moving_state", {}),
        "renovation_state": snapshot.get("renovation_state", {}),
        "active_skills": snapshot.get("active_skills", []),
    }
