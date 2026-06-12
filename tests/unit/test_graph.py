"""
测试 LangGraph 基础状态机（P0 Echo 节点）。

运行:
    pytest tests/unit/test_graph.py -v
"""

from __future__ import annotations

import pytest

from app.agent.graph import build_graph, run_echo
from app.agent.state import create_empty_state


# ═══════════════════════════════════════════════════════════
# 图编译测试
# ═══════════════════════════════════════════════════════════

class TestGraphCompilation:
    """测试图是否能正常编译。"""

    def test_build_graph_returns_compiled_graph(self):
        """build_graph() 应返回已编译的 StateGraph 实例。"""
        graph = build_graph()
        assert graph is not None
        # 编译后的图应有 invoke / ainvoke 方法
        assert hasattr(graph, "ainvoke"), "编译后的图应有 ainvoke 方法"
        assert hasattr(graph, "invoke"), "编译后的图应有 invoke 方法"

    def test_build_graph_is_idempotent(self):
        """多次调用 build_graph() 应返回不同的实例（无共享状态）。"""
        g1 = build_graph()
        g2 = build_graph()
        assert g1 is not g2


# ═══════════════════════════════════════════════════════════
# Echo 节点测试
# ═══════════════════════════════════════════════════════════

class TestEchoNode:
    """测试 echo 节点的输入输出。"""

    @pytest.mark.asyncio
    async def test_echo_returns_formatted_output(self, empty_master_state):
        """完整图流应产出装修+搬家方案。"""
        state = empty_master_state
        state["last_user_input"] = "三居室，90平，预算15万，从朝阳搬到海淀"
        state["session_id"] = "test_echo_001"

        graph = build_graph()
        result = await graph.ainvoke(state)

        assert result["final_response"] is not None
        # 经 moving_agent→renovation_agent→echo，输出含装修方案
        assert any(kw in result["final_response"] for kw in ["装修", "🏗️", "预算"])

    @pytest.mark.asyncio
    async def test_full_graph_increments_round(self, empty_master_state):
        """完整图流（intent→echo）应将 conversation_round +1。"""
        state = empty_master_state
        state["last_user_input"] = "test"
        state["conversation_round"] = 3

        graph = build_graph()
        result = await graph.ainvoke(state)

        assert result["conversation_round"] == 4

    @pytest.mark.asyncio
    async def test_echo_preserves_other_fields(self, empty_master_state):
        """Echo 节点不应清空 state 中其他已有字段。"""
        state = empty_master_state
        state["last_user_input"] = "test"
        state["session_id"] = "test_preserve_001"
        state["user_id"] = "user_999"
        state["moving_state"]["from_address"] = "北京朝阳"
        state["renovation_state"]["total_budget_yuan"] = 150000

        graph = build_graph()
        result = await graph.ainvoke(state)

        assert result["session_id"] == "test_preserve_001"
        assert result["user_id"] == "user_999"
        assert result["moving_state"]["from_address"] == "北京朝阳"
        assert result["renovation_state"]["total_budget_yuan"] == 150000

    @pytest.mark.asyncio
    async def test_echo_with_empty_input(self, empty_master_state):
        """Echo 节点应能处理空输入。"""
        state = empty_master_state
        state["last_user_input"] = ""

        graph = build_graph()
        result = await graph.ainvoke(state)

        assert "final_response" in result
        assert result["final_response"] is not None


# ═══════════════════════════════════════════════════════════
# 便捷方法测试
# ═══════════════════════════════════════════════════════════

class TestRunEcho:
    """测试 run_echo 便捷方法。"""

    @pytest.mark.asyncio
    async def test_run_echo_convenience(self):
        """run_echo 应返回装修方案（完整图流：moving→renovation→echo）。"""
        result = await run_echo(
            session_id="test_convenience_001",
            user_input="三居室，90平，预算15万",
        )

        assert result["session_id"] == "test_convenience_001"
        assert result["final_response"] is not None
        assert any(kw in result["final_response"] for kw in ["装修", "🏗️", "预算"])
        assert result["conversation_round"] == 1


# ═══════════════════════════════════════════════════════════
# 状态 Schema 测试
# ═══════════════════════════════════════════════════════════

class TestMasterState:
    """测试 MasterState 的数据完整性。"""

    def test_create_empty_state_has_all_fields(self):
        """create_empty_state 应包含架构设计中的所有必要字段。"""
        state = create_empty_state()

        # 会话元数据
        assert "session_id" in state
        assert "user_id" in state
        assert "conversation_round" in state
        assert state["conversation_round"] == 0

        # 子状态
        assert "moving_state" in state
        assert "renovation_state" in state
        assert state["moving_state"]["inventory"] == []
        assert state["renovation_state"]["construction_phases"] is None

        # Skill
        assert "active_skills" in state
        assert state["active_skills"] == []

        # RAG
        assert "rag_context" in state

        # 溯源（风险 8 优化）
        assert "recent_traces" in state
        assert "trace_ids" in state
        assert isinstance(state["recent_traces"], list)
        assert isinstance(state["trace_ids"], list)

        # 级联日志
        assert "cascade_log" in state

    def test_create_empty_state_is_independent(self):
        """多次调用 create_empty_state 应返回独立的状态对象。"""
        s1 = create_empty_state()
        s2 = create_empty_state()

        s1["moving_state"]["from_address"] = "北京"
        assert s2["moving_state"]["from_address"] is None  # s2 不受 s1 影响
