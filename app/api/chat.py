"""
对话 API — POST /api/chat (SSE 流式 / 非流式)。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.graph import build_graph
from app.agent.state import create_empty_state
from app.memory.session_store import get_session_store
from app.trace.evidence_builder import EvidenceBuilder

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    session_id: str | None = Field(None, description="会话 ID，新会话传 null")
    user_id: str | None = Field(None, description="用户 ID")
    message: str = Field(..., description="用户自然语言输入")
    stream: bool = Field(True, description="是否 SSE 流式返回")


class ChatResponse(BaseModel):
    session_id: str
    intent: str | None
    response: str
    active_skills: list[str] = []
    moving_summary: dict | None = None
    renovation_summary: dict | None = None
    recent_traces: list[str] = []
    tool_call_log: list[dict] = []


@router.post("/chat")
async def chat(req: ChatRequest):
    """用户对话端点（支持 SSE 流式）。"""
    session_id = req.session_id or f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{id(req)}"
    user_id = req.user_id or "anonymous"

    # 加载或创建状态
    store = get_session_store()
    state = await store.load_state(session_id)

    if state is None:
        state = create_empty_state(session_id=session_id, user_id=user_id)
        # 跨会话画像加载（模块 3）
        try:
            from app.agent.nodes.profile import load_user_profile
            profile = await load_user_profile(user_id)
            if profile:
                state["user_profile"] = profile
                logger.info(f"[chat] Loaded profile for user={user_id}: {list(profile.keys())}")
        except Exception as e:
            logger.warning(f"[chat] Profile load failed: {e}")

        # 跨会话历史方案加载（情景记忆）
        try:
            from app.memory.plan_store import load_latest_plan
            latest_plan = await load_latest_plan(user_id)
            if latest_plan:
                # 注入历史方案的 moving_state + renovation_state 作为初始值
                state["moving_state"] = {**state["moving_state"], **latest_plan.get("moving_state", {})}
                state["renovation_state"] = {**state["renovation_state"], **latest_plan.get("renovation_state", {})}
                state["active_skills"] = latest_plan.get("active_skills", [])
                state["_loaded_plan_id"] = latest_plan.get("plan_id")
                logger.info(
                    f"[chat] Loaded historical plan {latest_plan.get('plan_id')} "
                    f"v{latest_plan.get('plan_version')} for user={user_id}: "
                    f"{latest_plan.get('summary', '')[:80]}"
                )
        except Exception as e:
            logger.warning(f"[chat] Plan load failed (non-critical): {e}")
    else:
        # 恢复状态，确保必需字段存在
        state.setdefault("session_id", session_id)
        state.setdefault("user_id", user_id)

    state["last_user_input"] = req.message
    state["conversation_round"] = state.get("conversation_round", 0)

    if req.stream:
        return StreamingResponse(
            _stream_chat(session_id, state),
            media_type="text/event-stream",
        )

    # 非流式
    result = await _run_graph(state)
    await store.save_state(session_id, result)

    # 异步保存方案到历史（不阻塞响应）
    _schedule_plan_save(user_id, result, session_id)

    return _build_response(session_id, result)


async def _stream_chat(session_id: str, state: dict):
    """SSE 流式返回。"""
    try:
        # 发送开始事件
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.1)

        # 运行图
        graph = build_graph()

        # 先做意图分类（第一个节点）
        from app.agent.nodes.intent import IntentClassifyNode
        intent_node = IntentClassifyNode()
        intent_result = await intent_node(state)
        state.update(intent_result)

        yield f"data: {json.dumps({'type': 'intent', 'intent': state.get('intent'), 'confidence': state.get('intent_confidence', 0)}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.05)

        # 执行完整图
        result = await graph.ainvoke(state)

        # 保存状态
        store = get_session_store()
        await store.save_state(session_id, result)

        # 异步保存方案到历史
        user_id = state.get("user_id", "anonymous")
        _schedule_plan_save(user_id, result, session_id)

        # 发送响应
        resp = _build_response(session_id, result)
        yield f"data: {json.dumps({'type': 'complete', **resp.model_dump()}, ensure_ascii=False)}\n\n"

    except Exception as e:
        logger.error(f"SSE error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"


async def _run_graph(state: dict) -> dict:
    """执行 LangGraph 图。"""
    graph = build_graph()
    return await graph.ainvoke(state)


def _schedule_plan_save(user_id: str, result: dict, session_id: str) -> None:
    """异步保存方案到历史库（fire-and-forget，不阻塞响应）。"""
    if not user_id or user_id == "anonymous":
        return
    ms = result.get("moving_state", {})
    rs = result.get("renovation_state", {})
    if not ms.get("from_address") and not ms.get("total_volume_m3") and not rs.get("total_budget_yuan"):
        return  # 空方案，跳过
    try:
        from app.memory.plan_store import save_plan
        tools_called = [tc.get("tool", "") for tc in result.get("tool_call_log", [])]
        asyncio.create_task(save_plan(
            user_id=user_id,
            state=result,
            session_id=session_id,
            tools_called=tools_called,
        ))
    except Exception as e:
        logger.debug(f"[chat] Plan save schedule failed: {e}")


def _build_response(session_id: str, result: dict) -> ChatResponse:
    """构建 API 响应。"""
    eb = EvidenceBuilder()
    traces = eb.extract_traces_from_state(result)

    ms = result.get("moving_state", {})
    rs = result.get("renovation_state", {})

    return ChatResponse(
        session_id=session_id,
        intent=result.get("intent"),
        response=result.get("final_response", ""),
        active_skills=result.get("active_skills", []),
        moving_summary={
            "items": len(ms.get("inventory", [])),
            "volume_m3": ms.get("total_volume_m3"),
            "vehicle": ms.get("vehicle_recommendation", {}).get("type") if ms.get("vehicle_recommendation") else None,
            "freight_yuan": ms.get("freight_estimate", {}).get("total_estimate_yuan") if ms.get("freight_estimate") else None,
            "from_addr": ms.get("from_address"),
            "to_addr": ms.get("to_address"),
            "distance_km": ms.get("route", {}).get("distance_km") if ms.get("route") else None,
        } if (ms.get("total_volume_m3") or ms.get("from_address") or ms.get("vehicle_recommendation") or ms.get("freight_estimate")) else None,
        renovation_summary={
            "budget_yuan": rs.get("total_budget_yuan"),
            "hard_fixture_pct": rs.get("budget_allocation", {}).get("hard_fixture", {}).get("percentage") if rs.get("budget_allocation") else None,
            "total_days": sum(p.get("duration_days", 0) for p in rs.get("construction_phases", [])) if rs.get("construction_phases") else None,
            "materials_count": len(rs.get("materials", [])),
        } if rs.get("budget_allocation") else None,
        recent_traces=traces,
        tool_call_log=result.get("tool_call_log", []),
    )
