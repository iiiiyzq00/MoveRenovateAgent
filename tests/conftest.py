"""
pytest 全局 fixtures。

Usage:
    pytest tests/ -v
"""

from __future__ import annotations

import pytest

from app.agent.state import create_empty_state, MasterState


@pytest.fixture
def empty_master_state() -> MasterState:
    """空初始状态（模拟新会话）。"""
    return create_empty_state(
        session_id="test_session_001",
        user_id="test_user_001",
    )


@pytest.fixture
def sample_user_input() -> str:
    """示例用户输入。"""
    return "三居室，有猫，预算15万，朝阳搬到海淀"


@pytest.fixture
def incremental_input() -> str:
    """示例增量修改输入。"""
    return "预算提到18万，再加一架钢琴"
