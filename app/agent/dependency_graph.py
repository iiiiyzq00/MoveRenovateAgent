"""
声明式依赖图 — 运行时自动计算级联影响（风险 3 优化）。

替代硬编码 CASCADE_MAP，通过 register() 声明字段间依赖，
支持拓扑排序自动生成 affected_modules。

Usage:
    from app.agent.dependency_graph import dep_graph, DependencyGraph
    chain = dep_graph.get_cascade_chain("renovation_state.total_budget_yuan")
    # → ["renovation_state.total_budget_yuan", "renovation_state.budget_allocation", "moving_state.freight_estimate"]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from graphlib import TopologicalSorter

logger = logging.getLogger(__name__)


@dataclass
class FieldDependency:
    """单个字段的依赖声明。"""
    field_path: str
    depends_on: set[str] = field(default_factory=set)
    cascades_to: set[str] = field(default_factory=set)
    cross_agent: bool = False
    recompute_fn: str = ""       # 重算函数名
    module: str = ""             # 所属模块: "moving" | "renovation"


class DependencyGraph:
    """运行时依赖图 — 从字段声明自动构建。"""

    def __init__(self) -> None:
        self._fields: dict[str, FieldDependency] = {}
        self._reverse: dict[str, set[str]] = {}  # cascades_to 的反向索引

    def register(
        self,
        field_path: str,
        *,
        depends_on: set[str] | None = None,
        cascades_to: set[str] | None = None,
        cross_agent: bool = False,
        recompute_fn: str = "",
        module: str = "",
    ) -> FieldDependency:
        """注册字段及其依赖/级联关系。"""
        dep = FieldDependency(
            field_path=field_path,
            depends_on=depends_on or set(),
            cascades_to=cascades_to or set(),
            cross_agent=cross_agent,
            recompute_fn=recompute_fn,
            module=module,
        )
        self._fields[field_path] = dep

        # 维护反向索引
        for target in dep.cascades_to:
            self._reverse.setdefault(target, set()).add(field_path)

        return dep

    def get_cascade_chain(self, changed_field: str) -> list[str]:
        """
        输入变更字段，返回拓扑排序后的受影响字段列表。

        使用 BFS 遍历级联图，再拓扑排序。
        """
        visited: set[str] = set()
        queue: list[str] = [changed_field]
        affected: list[str] = []

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            dep = self._fields.get(current)
            if dep:
                affected.append(current)
                for cascaded in dep.cascades_to:
                    if cascaded not in visited:
                        queue.append(cascaded)

        return self._topo_sort(affected)

    def get_affected_modules(self, changed_fields: list[str]) -> dict[str, list[str]]:
        """按模块分组输出受影响的字段列表。"""
        all_affected: set[str] = set()
        for f in changed_fields:
            chain = self.get_cascade_chain(f)
            all_affected.update(chain)

        moving: list[str] = []
        renovation: list[str] = []
        for f in sorted(all_affected):
            if f.startswith("moving_state"):
                moving.append(f)
            elif f.startswith("renovation_state"):
                renovation.append(f)

        return {"moving": moving, "renovation": renovation}

    def get_cross_agent_signals(self, changed_fields: list[str]) -> list[dict]:
        """检测跨 Agent 的级联信号。"""
        signals: list[dict] = []
        for f in changed_fields:
            dep = self._fields.get(f)
            if dep and dep.cross_agent:
                signals.append({
                    "from_field": f,
                    "cascades_to": list(dep.cascades_to),
                    "recompute_fn": dep.recompute_fn,
                    "to_module": "renovation" if f.startswith("moving_state") else "moving",
                })
        return signals

    def _topo_sort(self, fields: list[str]) -> list[str]:
        """拓扑排序，确保被依赖者先算。"""
        graph: dict[str, set[str]] = {}
        for f in fields:
            dep = self._fields.get(f)
            graph[f] = dep.depends_on & set(fields) if dep else set()
        try:
            ts = TopologicalSorter(graph)
            return list(ts.static_order())
        except Exception:
            return fields  # 有环时返回原始顺序

    # ── 便捷查询 ──

    def what_depends_on(self, field_path: str) -> set[str]:
        """哪些字段依赖此字段（反向查询）。"""
        return self._reverse.get(field_path, set())

    def get_field(self, field_path: str) -> FieldDependency | None:
        return self._fields.get(field_path)


# ═══════════════════════════════════════════════════════════
# 全局实例（启动时构建）
# ═══════════════════════════════════════════════════════════

dep_graph = DependencyGraph()

# ── 搬家领域 ──
dep_graph.register("moving_state.inventory",
    cascades_to={"moving_state.total_volume_m3", "moving_state.total_weight_kg"},
    module="moving")
dep_graph.register("moving_state.total_volume_m3",
    depends_on={"moving_state.inventory"},
    cascades_to={"moving_state.vehicle_recommendation", "moving_state.box_plan"},
    module="moving")
dep_graph.register("moving_state.total_weight_kg",
    depends_on={"moving_state.inventory"},
    cascades_to={"moving_state.vehicle_recommendation"},
    module="moving")
dep_graph.register("moving_state.vehicle_recommendation",
    depends_on={"moving_state.total_volume_m3", "moving_state.total_weight_kg"},
    cascades_to={"moving_state.freight_estimate"},
    module="moving")
dep_graph.register("moving_state.freight_estimate",
    depends_on={"moving_state.vehicle_recommendation", "moving_state.route", "moving_state.move_date"},
    module="moving")
dep_graph.register("moving_state.box_plan",
    depends_on={"moving_state.total_volume_m3"},
    module="moving")
dep_graph.register("moving_state.route",
    depends_on={"moving_state.from_address", "moving_state.to_address"},
    module="moving")
dep_graph.register("moving_state.from_address",
    cascades_to={"moving_state.route", "moving_state.freight_estimate"},
    module="moving")
dep_graph.register("moving_state.to_address",
    cascades_to={"moving_state.route", "moving_state.freight_estimate"},
    module="moving")
dep_graph.register("moving_state.move_date",
    cascades_to={"moving_state.freight_estimate", "renovation_state.construction_phases"},
    cross_agent=True, module="moving")

# ── 装修领域 ──
dep_graph.register("renovation_state.total_budget_yuan",
    cascades_to={"renovation_state.budget_allocation", "moving_state.freight_estimate"},
    cross_agent=True, recompute_fn="renovation_agent.reallocate_budget",
    module="renovation")
dep_graph.register("renovation_state.budget_allocation",
    depends_on={"renovation_state.total_budget_yuan"},
    cascades_to={"renovation_state.materials"},
    module="renovation")
dep_graph.register("renovation_state.house_area_m2",
    cascades_to={"renovation_state.construction_phases", "renovation_state.materials"},
    module="renovation")
dep_graph.register("renovation_state.layout",
    cascades_to={"moving_state.inventory"},
    cross_agent=True, module="renovation")
dep_graph.register("renovation_state.construction_phases",
    depends_on={"renovation_state.house_area_m2", "moving_state.move_date"},
    cross_agent=True, module="renovation")
dep_graph.register("renovation_state.materials",
    depends_on={"renovation_state.construction_phases", "renovation_state.budget_allocation"},
    module="renovation")
dep_graph.register("renovation_state.style_preference",
    cascades_to={"renovation_state.materials"},
    module="renovation")
dep_graph.register("renovation_state.move_in_condition",
    cascades_to={"renovation_state.construction_phases"},
    module="renovation")
