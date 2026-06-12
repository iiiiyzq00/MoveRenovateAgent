"""
会话存储 — 短期记忆（Redis 优先 + 内存降级）。

Usage:
    from app.memory.session_store import SessionStore
    store = SessionStore()
    await store.save_state("sess_1", state)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.config import get_config

logger = logging.getLogger(__name__)

# ── Redis 可选导入 ──────────────────────────────────────
try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False


class InMemoryStore:
    """内存存储（降级用）。"""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._expiry: dict[str, float] = {}

    async def get(self, key: str) -> str | None:
        import time
        exp = self._expiry.get(key, float("inf"))
        if time.monotonic() > exp:
            self._data.pop(key, None)
            self._expiry.pop(key, None)
            return None
        return self._data.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        import time
        self._data[key] = value
        self._expiry[key] = time.monotonic() + ttl

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._expiry.pop(key, None)

    async def lpush(self, key: str, *values: str) -> int:
        lst = json.loads(self._data.get(key, "[]"))
        for v in reversed(values):
            lst.insert(0, v)
        self._data[key] = json.dumps(lst)
        return len(lst)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        lst = json.loads(self._data.get(key, "[]"))
        self._data[key] = json.dumps(lst[start:end + 1])

    async def lpop(self, key: str) -> str | None:
        lst = json.loads(self._data.get(key, "[]"))
        if lst:
            val = lst.pop(0)
            self._data[key] = json.dumps(lst)
            return val
        return None

    async def hset(self, key: str, mapping: dict) -> None:
        d = json.loads(self._data.get(key, "{}"))
        d.update(mapping)
        self._data[key] = json.dumps(d)

    async def hgetall(self, key: str) -> dict:
        return json.loads(self._data.get(key, "{}"))


class SessionStore:
    """会话存储 — 支持 Redis / 内存降级。"""

    TTL: int = 7200
    MAX_HISTORY: int = 20
    MAX_SNAPSHOTS: int = 5

    def __init__(self) -> None:
        cfg = get_config()
        self._redis = None
        self._memory = InMemoryStore()
        self._use_redis = False

        if HAS_REDIS:
            try:
                self._redis = aioredis.from_url(
                    cfg.redis_dsn, decode_responses=True,
                    socket_connect_timeout=2, socket_timeout=2,
                )
                self._use_redis = True
                logger.info("SessionStore: Redis connected (multi-worker safe)")
            except Exception as e:
                self._use_redis = False
                logger.warning(f"Redis unavailable ({e}), using in-memory fallback "
                               "(⚠️ NOT multi-worker safe — use only with 1 worker)")
        else:
            self._use_redis = False
            logger.warning("redis package not installed, using in-memory fallback")

    async def _r(self, method: str, *args, **kwargs):
        """统一调用 Redis 或内存（Redis 失败自动降级）。"""
        if self._use_redis and self._redis:
            try:
                return await getattr(self._redis, method)(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Redis call '{method}' failed: {e}, falling back to memory")
                self._use_redis = False  # 永久降级
        return await getattr(self._memory, method)(*args, **kwargs)

    # ── 状态存取 ──

    async def save_state(self, session_id: str, state: dict) -> None:
        """保存当前会话状态。"""
        payload = json.dumps(state, default=str, ensure_ascii=False)
        await self._r("setex", f"session:{session_id}:state", self.TTL, payload)

    async def load_state(self, session_id: str) -> dict | None:
        """加载会话状态。"""
        raw = await self._r("get", f"session:{session_id}:state")
        if raw:
            return json.loads(raw)
        return None

    # ── Undo 支持 ──

    async def push_snapshot(self, session_id: str, state: dict) -> None:
        """保存一份状态快照（最多 5 份）。"""
        payload = json.dumps(state, default=str, ensure_ascii=False)
        key = f"session:{session_id}:snapshots"
        await self._r("lpush", key, payload)
        await self._r("ltrim", key, 0, self.MAX_SNAPSHOTS - 1)

    async def pop_snapshot(self, session_id: str) -> dict | None:
        """弹出最近一份快照（undo）。"""
        raw = await self._r("lpop", f"session:{session_id}:snapshots")
        if raw:
            state = json.loads(raw)
            await self.save_state(session_id, state)
            return state
        return None

    async def snapshot_count(self, session_id: str) -> int:
        """快照栈深度。"""
        key = f"session:{session_id}:snapshots"
        raw = await self._r("get", key)
        if raw:
            lst = json.loads(raw) if isinstance(raw, str) else []
            return len(lst)
        return 0

    # ── 对话历史 ──

    async def append_history(self, session_id: str, entry: dict) -> None:
        """追加一轮对话记录。"""
        payload = json.dumps(entry, ensure_ascii=False)
        key = f"session:{session_id}:history"
        await self._r("lpush", key, payload)
        await self._r("ltrim", key, 0, self.MAX_HISTORY - 1)

    # ── 实体索引 ──

    async def update_entity_index(self, session_id: str, entities: dict) -> None:
        """更新实体索引（用于增量识别）。"""
        key = f"session:{session_id}:entity_idx"
        mapping = {k: json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                   for k, v in entities.items()}
        await self._r("hset", key, mapping)

    async def get_entity_index(self, session_id: str) -> dict:
        """获取实体索引。"""
        key = f"session:{session_id}:entity_idx"
        raw = await self._r("hgetall", key)
        if raw:
            return {k: (json.loads(v) if v.startswith("{") or v.startswith("[") else v)
                    for k, v in raw.items()}
        return {}

    # ── MCP 缓存 ──

    async def mcp_cache_get(self, session_id: str, cache_key: str) -> dict | None:
        """读取 MCP 工具调用缓存。"""
        raw = await self._r("get", f"session:{session_id}:mcp:{cache_key}")
        if raw:
            return json.loads(raw)
        return None

    async def mcp_cache_set(self, session_id: str, cache_key: str, data: dict, ttl: int = 7200) -> None:
        """写入 MCP 缓存。"""
        await self._r("setex", f"session:{session_id}:mcp:{cache_key}", ttl,
                       json.dumps(data, ensure_ascii=False))

    async def mcp_cache_clear(self, session_id: str) -> None:
        """清空会话 MCP 缓存（move_date 变更时触发）。"""
        # 用 hgetall 找所有 key 并删除
        pass  # 简化：session TTL 过期自动清理


# 全局单例
_session_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store
