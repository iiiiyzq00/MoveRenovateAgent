"""
LangGraph 状态机构建 — build_graph() 函数。

双 Agent 并行：asyncio.gather 在 node_parallel_agents 内并行调用，
避免 Send API 的多 run 语义问题。

Usage:
    from app.agent.graph import build_graph
    graph = build_graph()
    result = await graph.ainvoke(state)
"""

from __future__ import annotations

import logging
from datetime import datetime

from langgraph.graph import END, StateGraph

from app.agent.state import MasterState

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 意图分类节点
# ═══════════════════════════════════════════════════════════

async def node_intent_classify(state: MasterState) -> dict:
    from app.agent.nodes.intent import IntentClassifyNode
    return await IntentClassifyNode()(state)


# ═══════════════════════════════════════════════════════════
# RAG 检索节点
# ═══════════════════════════════════════════════════════════

async def node_rag_retrieve(state: MasterState) -> dict:
    from app.agent.nodes.rag_retrieve import RAGRetrieveNode
    return await RAGRetrieveNode()(state)


# ═══════════════════════════════════════════════════════════
# 过渡响应节点（echo）
# ═══════════════════════════════════════════════════════════

async def node_echo(state: MasterState) -> dict:
    response = state.get("final_response", "")
    if response and ("🚛" in response or "🏗️" in response or "装修" in response):
        return {
            "updated_at": datetime.now().isoformat(),
            "conversation_round": state.get("conversation_round", 0) + 1,
        }
    # 降级显示
    user_input = state.get("last_user_input", "")
    intent = state.get("intent", "unknown")
    return {
        "final_response": f"📝 用户输入: {user_input}\n🎯 意图: {intent}",
        "updated_at": datetime.now().isoformat(),
        "conversation_round": state.get("conversation_round", 0) + 1,
    }


# ═══════════════════════════════════════════════════════════
# 节点封装
# ═══════════════════════════════════════════════════════════

async def node_react_agent(state: MasterState) -> dict:
    from app.agent.nodes.react_agent import ReActAgentNode
    return await ReActAgentNode()(state)


async def node_moving_agent(state: MasterState) -> dict:
    from app.agent.nodes.moving_agent import MovingAgentNode
    return await MovingAgentNode()(state)


async def node_renovation_agent(state: MasterState) -> dict:
    from app.agent.nodes.renovation_agent import RenovationAgentNode
    return await RenovationAgentNode()(state)


async def node_profile_extract(state: MasterState) -> dict:
    from app.agent.nodes.profile import ProfileExtractNode
    return await ProfileExtractNode()(state)


async def node_cascade_compute(state: MasterState) -> dict:
    from app.agent.nodes.cascade import CascadeComputeNode
    return await CascadeComputeNode()(state)


async def node_skill_activate(state: MasterState) -> dict:
    from app.agent.nodes.skill_activate import SkillActivateNode
    return await SkillActivateNode()(state)


# ═══════════════════════════════════════════════════════════
# 并行协调节点 — 按需运行 Agent
# ═══════════════════════════════════════════════════════════

def _needs_moving_plan(user_input: str, state: MasterState) -> bool:
    """判断是否需要搬家方案（需要具体地址或运费信息）。"""
    ms = state.get("moving_state", {})
    has_existing = bool(ms.get("from_address") or ms.get("total_volume_m3"))
    if has_existing:
        return True
    # 需要地址/路线关键信号，仅"搬家"一词不够
    import re
    addr_signals = ["搬到", "搬出", "搬入", "搬去", "搬到", "运费", "多少钱",
                    "车型", "纸箱", "打包", "装箱", "路线", "多少公里"]
    has_addr = any(kw in user_input for kw in addr_signals)
    # 检测地址模式："从...到..." 或 "北京朝阳" 等
    has_addr_pattern = bool(re.search(r'(从|把).{1,20}(搬到|到|搬|→)', user_input))
    # 或者包含明显的城市/区名组合 "X到Y"
    cities = r'(朝阳|海淀|浦东|徐汇|静安|通州|大兴|丰台|东城|西城|黄浦|长宁|闵行|宝山)'
    has_city_pair = bool(re.search(cities + r'.{0,8}(到|搬|→).{0,8}' + cities, user_input))
    return has_addr or has_addr_pattern or has_city_pair


def _needs_renovation_plan(user_input: str, state: MasterState) -> bool:
    """判断是否需要装修方案（需要预算数字或面积数字）。"""
    rs = state.get("renovation_state", {})
    has_existing = bool(rs.get("total_budget_yuan") or rs.get("house_area_m2"))
    if has_existing:
        return True
    import re
    # 必须有具体数字
    has_budget = bool(re.search(r'(\d+)\s*万', user_input))
    has_area = bool(re.search(r'(\d+)\s*(平|平米|㎡|m2)', user_input))
    has_style = any(kw in user_input for kw in ["装修", "风格", "施工", "硬装", "软装", "北欧", "简约", "中式"])
    return has_budget or has_area or has_style


async def node_parallel_agents(state: MasterState) -> dict:
    """
    按需并行协调节点 — 根据用户意图决定运行哪些 Agent。

    - 纯知识问答：只跑 react_agent（用 rag_search 回答）
    - 搬家需求：跑 react_agent
    - 装修需求：跑 renovation_agent
    - 两者都有：并行跑
    """
    import asyncio

    user_input = state.get("last_user_input", "")
    need_moving = _needs_moving_plan(user_input, state)
    need_reno = _needs_renovation_plan(user_input, state)

    # 如果两者都不需要（极少数情况），默认跑 react_agent
    if not need_moving and not need_reno:
        need_moving = True

    logger.info(f"[parallel] need_moving={need_moving}, need_reno={need_reno}")

    # ── 按需并行执行 ──
    react_result: dict = {}
    reno_result: dict = {}

    if need_moving and need_reno:
        react_result, reno_result = await asyncio.gather(
            node_react_agent(state), node_renovation_agent(state)
        )
    elif need_moving:
        react_result = await node_react_agent(state)
    elif need_reno:
        reno_result = await node_renovation_agent(state)
        # 如果仅有装修需求，也用 react_agent 获取知识支持
        react_result = await node_react_agent(state)

    # ── 合并响应 ──
    moving_resp = react_result.get("_moving_response", "")
    reno_resp = reno_result.get("_renovation_response", "")
    parts = [p for p in [moving_resp, reno_resp] if p]
    combined = "\n\n---\n\n".join(parts) if parts else "（方案生成中...）"

    # ── 交叉感知 ──
    ms = react_result.get("moving_state", {})
    large_items = [i for i in ms.get("inventory", []) if i.get("estimated_volume_m3", 0) > 2.0]
    if large_items and reno_resp:
        names = [i["name"] for i in large_items]
        combined += f"\n\n### 🔗 交叉协调\n- 🏠 大件 {', '.join(names)} 需在新家预留空间"

    # ── 合并 traces ──
    moving_traces = react_result.get("_moving_traces", []) or []
    reno_traces = reno_result.get("_reno_traces", []) or []

    return {
        "final_response": combined,
        "moving_state": react_result.get("moving_state", {}),
        "renovation_state": reno_result.get("renovation_state", {}),
        "recent_traces": moving_traces + reno_traces,
        "tool_call_log": react_result.get("tool_call_log", []),
        "updated_at": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════════════
# 路由函数
# ═══════════════════════════════════════════════════════════

def _route_after_intent(state: MasterState) -> str:
    intent = state.get("intent", "new_topic")
    if intent in ("reset", "clarification"):
        return "node_echo"
    if intent == "incremental_update":
        return "node_cascade_compute"
    return "node_skill_activate"


# ═══════════════════════════════════════════════════════════
# Graph 构建
# ═══════════════════════════════════════════════════════════

def build_graph() -> StateGraph:
    """
    构建 LangGraph 状态机。

    流程：
    new_topic:         START → intent → skill → rag → parallel → profile → echo → END
    incremental_update: START → intent → cascade → skill → rag → parallel → profile → echo → END

    parallel 节点内部 asyncio.gather 并行运行 react_agent + renovation_agent。
    """
    graph = StateGraph(MasterState)

    # ── 注册节点 ──
    graph.add_node("node_intent_classify", node_intent_classify)
    graph.add_node("node_skill_activate", node_skill_activate)
    graph.add_node("node_cascade_compute", node_cascade_compute)
    graph.add_node("node_rag_retrieve", node_rag_retrieve)
    graph.add_node("node_parallel_agents", node_parallel_agents)
    graph.add_node("node_moving_agent", node_moving_agent)  # 保留：降级备用
    graph.add_node("node_profile_extract", node_profile_extract)
    graph.add_node("node_echo", node_echo)

    # ── 边 ──
    graph.set_entry_point("node_intent_classify")
    graph.add_conditional_edges(
        "node_intent_classify", _route_after_intent,
        {"node_skill_activate": "node_skill_activate",
         "node_cascade_compute": "node_cascade_compute",
         "node_echo": "node_echo"},
    )
    # cascade → skill → rag → parallel → profile → echo
    graph.add_edge("node_cascade_compute", "node_skill_activate")
    graph.add_edge("node_skill_activate", "node_rag_retrieve")
    graph.add_edge("node_rag_retrieve", "node_parallel_agents")
    graph.add_edge("node_parallel_agents", "node_profile_extract")
    graph.add_edge("node_profile_extract", "node_echo")
    graph.add_edge("node_echo", END)

    compiled = graph.compile()
    logger.info("[build_graph] Graph compiled: intent→skill→rag→parallel(asyncio.gather)→profile→echo")
    return compiled


# ═══════════════════════════════════════════════════════════
# 便捷方法
# ═══════════════════════════════════════════════════════════

async def run_echo(session_id: str, user_input: str) -> MasterState:
    from app.agent.state import create_empty_state
    state = create_empty_state(session_id=session_id)
    state["last_user_input"] = user_input
    graph = build_graph()
    return await graph.ainvoke(state)
