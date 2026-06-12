"""
LangGraph 全局状态 Schema — MasterState TypedDict。

基于第二阶段 4.1 设计，包含风险 8 优化后的 recent_traces / trace_ids。

Usage:
    from app.agent.state import MasterState, MovingState, RenovationState
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from typing_extensions import TypedDict


# ═══════════════════════════════════════════════════════════
# 搬家子状态
# ═══════════════════════════════════════════════════════════

class MovingState(TypedDict, total=False):
    """搬家 Agent 的专属状态。"""

    from_address: str | None
    to_address: str | None
    move_date: str | None
    # 物品清单: [{item_id, name, category, quantity, fragile, estimated_volume_m3, special_handling, status}]
    inventory: list[dict[str, Any]]
    total_volume_m3: float
    total_weight_kg: float | None
    # 装箱方案: {small_boxes, medium_boxes, large_boxes, wardrobe_boxes}
    box_plan: dict[str, int]
    # 车型推荐: {type, capacity_m3, pet_friendly, ...}
    vehicle_recommendation: dict[str, Any] | None
    # 运费估算: {min_yuan, max_yuan, breakdown: {...}, uncertainty: str}
    freight_estimate: dict[str, Any] | None
    # 路线信息: {distance_km, duration_min, toll_yuan, restrictions: [...]}
    route: dict[str, Any] | None
    # 打包顺序: [{phase, items, box_type, label}]
    packing_sequence: list[dict[str, Any]] | None
    last_updated_round: int


# ═══════════════════════════════════════════════════════════
# 装修子状态
# ═══════════════════════════════════════════════════════════

class RenovationState(TypedDict, total=False):
    """装修 Agent 的专属状态。"""

    house_area_m2: float | None
    layout: str | None
    move_in_condition: str | None  # 毛坯 / 简装 / 精装翻新
    total_budget_yuan: float | None
    # 预算分配: {hard_fixture: {amount, pct}, soft_furnishing: ..., appliances: ..., reserve: ...}
    budget_allocation: dict[str, Any] | None
    # 施工阶段: [{phase, start_day, duration, deps, status}]
    construction_phases: list[dict[str, Any]] | None
    # 材料清单: [{name, spec, qty, unit_price, total, phase, brand_rec}]
    materials: list[dict[str, Any]] | None
    style_preference: str | None
    special_needs: list[str] | None
    last_updated_round: int


# ═══════════════════════════════════════════════════════════
# RAG 上下文
# ═══════════════════════════════════════════════════════════

class RAGContext(TypedDict, total=False):
    """RAG 检索上下文。"""

    query_type: str | None  # 7 类之一
    retrieved_docs: list[dict[str, Any]]  # [{doc_id, content, score, metadata}]
    threshold_used: float
    warning: str | None  # low_confidence / no_knowledge
    rag_priority: str | None  # required / optional / none


# ═══════════════════════════════════════════════════════════
# 决策溯源 — 轻量化（风险 8 优化）
# ═══════════════════════════════════════════════════════════

class EvidenceItem(TypedDict, total=False):
    """单条证据。"""
    evidence_id: str
    type: Literal["tool_result", "rag_reference", "calculation_rule"]
    content: str
    source_ref: str | None
    confidence: float


# ═══════════════════════════════════════════════════════════
# 全局主状态
# ═══════════════════════════════════════════════════════════

class MasterState(TypedDict, total=False):
    """
    全局主状态 Schema。

    设计要点（第二阶段 4.1 + 风险 8 优化）：
    - recent_traces: 最近 5 条结论摘要（≤100 字/条），替代原 decision_traces
    - trace_ids: 最近 20 条 trace_id，完整溯源按需从 PG 加载
    - skill_context_versions: {skill_id: version} 请求级版本绑定（风险 5）
    - _rag_prejudge: 意图分类阶段产出的 RAG 预判，传递到检索节点（风险 6）
    """

    # ── 会话元数据 ──
    session_id: str
    user_id: str
    conversation_round: int
    created_at: str
    updated_at: str

    # ── 对话 ──
    messages: Annotated[Sequence[BaseMessage], add_messages]
    last_user_input: str
    parsed_entities: list[dict[str, Any]]  # 从最新输入提取的实体

    # ── 意图与路由 ──
    intent: Literal["new_topic", "incremental_update", "clarification", "reset"] | None
    intent_confidence: float
    affected_modules: list[str]  # 级联影响模块列表

    # ── 子状态 ──
    moving_state: MovingState
    renovation_state: RenovationState

    # ── Skill ──
    active_skills: list[str]  # 当前激活的 skill_id 列表
    skill_context: str | None  # 合并后的 SOP 片段
    skill_context_versions: dict[str, str]  # {skill_id: version} 请求级绑定（风险 5）

    # ── RAG ──
    rag_context: RAGContext | None
    _rag_prejudge: dict[str, Any] | None  # 意图分类 → RAG 检索的内部传递（风险 6）

    # ── 记忆 ──
    user_profile: dict[str, Any] | None  # 从 PG 加载的长期画像

    # ── 溯源 — 轻量化（风险 8） ──
    recent_traces: list[str]  # 最近 5 条结论摘要（≤100 字/条）
    trace_ids: list[str]  # 最近 20 条 trace_id

    # ── 级联日志 ──
    cascade_log: list[dict[str, Any]]  # [{trigger_round, trigger_entity, affected_fields, recomputed}]

    # ── ReAct 工具调用日志 ──
    tool_call_log: list[dict[str, Any]]  # [{iteration, tool, args, result_summary}]

    # ── 响应 ──
    final_response: str | None
    delta_summary: str | None  # 增量变更摘要（仅 incremental_update 时）
    response_metadata: dict[str, Any] | None  # {format, tokens_used, tools_called}


# ═══════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════

def create_empty_state(
    session_id: str = "",
    user_id: str = "",
) -> MasterState:
    """创建空的初始状态（新会话）。"""
    from datetime import datetime

    now = datetime.now().isoformat()
    return MasterState(
        session_id=session_id,
        user_id=user_id,
        conversation_round=0,
        created_at=now,
        updated_at=now,
        messages=[],
        last_user_input="",
        parsed_entities=[],
        intent=None,
        intent_confidence=0.0,
        affected_modules=[],
        moving_state=MovingState(
            from_address=None,
            to_address=None,
            move_date=None,
            inventory=[],
            total_volume_m3=0.0,
            total_weight_kg=None,
            box_plan={},
            vehicle_recommendation=None,
            freight_estimate=None,
            route=None,
            packing_sequence=None,
            last_updated_round=0,
        ),
        renovation_state=RenovationState(
            house_area_m2=None,
            layout=None,
            move_in_condition=None,
            total_budget_yuan=None,
            budget_allocation=None,
            construction_phases=None,
            materials=None,
            style_preference=None,
            special_needs=None,
            last_updated_round=0,
        ),
        active_skills=[],
        skill_context=None,
        skill_context_versions={},
        rag_context=None,
        _rag_prejudge=None,
        user_profile=None,
        recent_traces=[],
        trace_ids=[],
        cascade_log=[],
        tool_call_log=[],
        final_response=None,
        delta_summary=None,
        response_metadata=None,
    )
