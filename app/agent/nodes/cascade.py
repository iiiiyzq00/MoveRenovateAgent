"""
级联分析节点 — node_cascade_compute + node_cascade_coordinate。

基于风险 3（声明式依赖图）+ 风险 2（并行写入冲突解决）。

Usage:
    from app.agent.nodes.cascade import CascadeComputeNode, CascadeCoordinateNode
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.agent.dependency_graph import dep_graph
from app.agent.state import MasterState

logger = logging.getLogger(__name__)


class CascadeComputeNode:
    """
    级联影响分析节点 — 基于声明式依赖图（风险 3）。

    输入 incremental_update + parsed_entities
    → 查询依赖图 → 输出 affected_modules + 拓扑排序重算顺序
    """

    def __init__(self) -> None:
        self.graph = dep_graph

    async def __call__(self, state: MasterState) -> dict:
        """计算受影响的模块。"""
        entities = state.get("parsed_entities", [])
        if not entities:
            return {"affected_modules": []}

        # 提取变更的字段路径
        changed_fields = [e["key"] for e in entities if e.get("action") in ("MODIFY", "APPEND", "DELETE")]

        if not changed_fields:
            return {"affected_modules": []}

        # 查询依赖图
        modules = self.graph.get_affected_modules(changed_fields)
        cross_signals = self.graph.get_cross_agent_signals(changed_fields)

        moving_count = len(modules.get("moving", []))
        reno_count = len(modules.get("renovation", []))

        # 构建 affected_modules 列表（供 graph 路由）
        affected = []
        if moving_count > 0:
            affected.append("moving")
        if reno_count > 0:
            affected.append("renovation")

        logger.info(
            f"[cascade] Changed: {changed_fields} → "
            f"Moving: {moving_count} fields, Renovation: {reno_count} fields, "
            f"Cross: {len(cross_signals)} signals"
        )

        return {
            "affected_modules": affected,
            "cascade_log": state.get("cascade_log", []) + [{
                "round": state["conversation_round"],
                "trigger_entities": [e["key"] for e in entities],
                "affected_moving_fields": modules.get("moving", []),
                "affected_renovation_fields": modules.get("renovation", []),
                "cross_agent_signals": cross_signals,
                "timestamp": datetime.now().isoformat(),
            }],
        }


# ═══════════════════════════════════════════════════════════
# 并行协调节点（风险 2 优化）
# ═══════════════════════════════════════════════════════════

class CascadeCoordinateNode:
    """
    并行协调节点 — 解决双 Agent 写入冲突。

    字段所有权表：决定交叉字段冲突时谁胜出。
    """

    FIELD_OWNERSHIP: dict[str, str] = {
        "moving_state.freight_estimate": "moving_agent",
        "moving_state.inventory": "moving_agent",
        "moving_state.vehicle_recommendation": "moving_agent",
        "moving_state.box_plan": "moving_agent",
        "moving_state.route": "moving_agent",
        "renovation_state.budget_allocation": "renovation_agent",
        "renovation_state.materials": "renovation_agent",
        "renovation_state.construction_phases": "renovation_agent",
        "renovation_state.total_budget_yuan": "renovation_agent",
    }

    CROSS_MERGE_STRATEGY: dict[str, str] = {
        # 交叉字段：哪个 Agent 写，另一个需感知
        "moving_state.freight_estimate": "last_write_wins",
        "renovation_state.total_budget_yuan": "last_write_wins",
    }

    async def coordinate(
        self,
        state: MasterState,
        moving_output: dict | None = None,
        renovation_output: dict | None = None,
    ) -> dict:
        """
        P0 简化版：顺序执行（moving → renovation），无冲突。

        P1 并行版（未来）：合并两方输出，按所有权 + merge_strategy 裁决冲突。
        """
        # P0: moving 和 renovation 顺序执行，renovation 自然胜出
        # 只需记录交叉感知
        signals = []
        ms = state.get("moving_state", {})
        rs = state.get("renovation_state", {})

        # 交叉检查：大件物品空间预留
        large_items = [i for i in ms.get("inventory", []) if i.get("estimated_volume_m3", 0) > 2.0]
        if large_items:
            names = [i["name"] for i in large_items]
            signals.append({
                "type": "large_item_placement",
                "items": names,
                "note": "请装修 Agent 确认此物品在搬入后空间是否预留",
            })

        return {
            "cascade_log": state.get("cascade_log", []) + [{
                "round": state.get("conversation_round", 0),
                "cross_signals": signals,
                "timestamp": datetime.now().isoformat(),
            }],
        }
