"""
Skill 注册表 — 版本化 + RCU 并发安全（风险 5 优化）。

Usage:
    from app.skill.registry import SkillRegistry
    registry = SkillRegistry()
    await registry.scan_directory("skills/")
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SkillShortDesc:
    """短描述 — 路由匹配用（常驻上下文）。"""
    skill_id: str
    display_name: str
    summary: str
    trigger_keywords: list[str]
    trigger_examples: list[str]
    required_tools: list[str]


@dataclass
class SkillSOP:
    """完整 SOP — 命中后才加载。"""
    skill_id: str
    version: str
    display_name: str
    trigger: dict[str, Any]
    preconditions: dict[str, Any]
    tools: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    output: dict[str, Any]
    exceptions: list[dict[str, Any]]
    test_cases: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SkillVersion:
    """版本化 Skill 快照（RCU 模式）。"""
    skill_id: str
    version: str
    sop: SkillSOP
    short_desc: SkillShortDesc
    loaded_at: float = field(default_factory=time.monotonic)
    ref_count: int = 0
    deprecated: bool = False


class SkillRegistry:
    """并发安全的版本化 SkillRegistry。"""

    def __init__(self) -> None:
        self._versions: dict[str, list[SkillVersion]] = defaultdict(list)
        self._latest: dict[str, str] = {}
        self._short_descs: dict[str, SkillShortDesc] = {}
        self._keyword_index: dict[str, set[str]] = defaultdict(set)
        self._rw_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pending_gc: list[SkillVersion] = []
        self._gc_lock = asyncio.Lock()

    # ── 扫描加载 ──────────────────────────────────────

    async def scan_directory(self, path: str = "skills/") -> int:
        """扫描 skills/ 目录，加载所有 skill.yaml。"""
        skills_dir = Path(path)
        if not skills_dir.exists():
            logger.warning(f"Skills directory not found: {path}")
            return 0

        count = 0
        for yaml_file in skills_dir.glob("*/skill.yaml"):
            skill_id = yaml_file.parent.name
            version = await self.reload(skill_id)
            if version:
                count += 1

        logger.info(f"Loaded {count} skills from {path}")
        return count

    async def reload(self, skill_id: str) -> str | None:
        """热加载单个 Skill — 创建新版本。"""
        yaml_path = Path(f"skills/{skill_id}/skill.yaml")
        if not yaml_path.exists():
            logger.warning(f"Skill YAML not found: {yaml_path}")
            return None

        async with self._rw_locks[skill_id]:
            try:
                with open(yaml_path, encoding="utf-8") as f:
                    raw = yaml.safe_load(f)

                sop = self._parse_sop(raw)
                short = self._parse_short_desc(raw)

                # 检查是否重复
                if self._latest.get(skill_id) == sop.version:
                    logger.debug(f"Skill {skill_id} v{sop.version} already loaded")
                    return sop.version

                # 创建版本快照
                sv = SkillVersion(
                    skill_id=skill_id,
                    version=sop.version,
                    sop=sop,
                    short_desc=short,
                )

                # 追加到版本栈
                self._versions[skill_id].append(sv)
                old_ver = self._latest.get(skill_id)
                self._latest[skill_id] = sop.version

                # 更新索引
                self._short_descs[skill_id] = short
                self._update_keyword_index(skill_id, old_ver, sop)

                # 标记旧版本
                for v in self._versions[skill_id][:-1]:
                    v.deprecated = True
                    if v.ref_count <= 0:
                        self._pending_gc.append(v)

                # 保留最近 3 个版本
                if len(self._versions[skill_id]) > 3:
                    old = self._versions[skill_id].pop(0)
                    if old.ref_count <= 0:
                        self._pending_gc.append(old)

                logger.info(f"🔥 Hot-reloaded Skill: {skill_id} v{sop.version}")
                return sop.version

            except Exception as e:
                logger.error(f"Failed to reload {skill_id}: {e}")
                return None

    def unregister(self, skill_id: str) -> None:
        """移除 Skill。"""
        if skill_id in self._versions:
            del self._versions[skill_id]
        self._latest.pop(skill_id, None)
        self._short_descs.pop(skill_id, None)
        for kw in list(self._keyword_index.keys()):
            self._keyword_index[kw].discard(skill_id)
            if not self._keyword_index[kw]:
                del self._keyword_index[kw]
        logger.info(f"🗑️ Skill removed: {skill_id}")

    # ── 读路径 ────────────────────────────────────────

    def get_latest_version(self, skill_id: str) -> SkillVersion | None:
        """获取最新版本（调用者负责 release）。"""
        versions = self._versions.get(skill_id, [])
        if not versions:
            return None
        v = versions[-1]
        v.ref_count += 1
        return v

    def release(self, sv: SkillVersion) -> None:
        """释放引用。"""
        sv.ref_count -= 1
        if sv.ref_count <= 0 and sv.deprecated:
            self._pending_gc.append(sv)
            asyncio.create_task(self._gc_if_idle())

    def get_short_descs(self) -> list[SkillShortDesc]:
        """获取所有短描述（用于路由匹配）。"""
        return list(self._short_descs.values())

    def get_keyword_index(self) -> dict[str, set[str]]:
        """获取关键词倒排索引。"""
        return dict(self._keyword_index)

    def get_sop(self, skill_id: str, version: str | None = None) -> SkillSOP | None:
        """获取指定版本 SOP。"""
        if version:
            for v in self._versions.get(skill_id, []):
                if v.version == version:
                    return v.sop
            return None
        # 最新版本
        latest = self.get_latest_version(skill_id)
        if latest:
            sop = latest.sop
            self.release(latest)
            return sop
        return None

    # ── 上下文管理 ────────────────────────────────────

    @asynccontextmanager
    async def bind_context(self, skill_ids: list[str]):
        """为一次请求绑定 Skill 版本上下文（自动 release）。"""
        ctx = SkillContext(self)
        for sid in skill_ids:
            ver = self.get_latest_version(sid)
            if ver:
                ctx._versions[sid] = ver
        try:
            yield ctx
        finally:
            ctx.release_all()

    # ── 内部方法 ──────────────────────────────────────

    def _parse_sop(self, raw: dict) -> SkillSOP:
        return SkillSOP(
            skill_id=raw["skill_id"],
            version=raw.get("version", "1.0.0"),
            display_name=raw.get("skill_name", raw["skill_id"]),
            trigger=raw.get("trigger", {}),
            preconditions=raw.get("preconditions", {}),
            tools=raw.get("tools", []),
            constraints=raw.get("constraints", []),
            output=raw.get("output", {}),
            exceptions=raw.get("exceptions", []),
            test_cases=raw.get("test_cases", []),
        )

    def _parse_short_desc(self, raw: dict) -> SkillShortDesc:
        sd = raw.get("short_description", {})
        return SkillShortDesc(
            skill_id=raw["skill_id"],
            display_name=sd.get("display_name", raw["skill_id"]),
            summary=sd.get("summary", ""),
            trigger_keywords=sd.get("trigger_keywords", raw.get("trigger", {}).get("keywords", [])),
            trigger_examples=sd.get("trigger_examples", []),
            required_tools=sd.get("required_tools", []),
        )

    def _update_keyword_index(self, skill_id: str, old_version: str | None, new_sop: SkillSOP) -> None:
        """增量更新关键词倒排索引。"""
        # 移除旧版本关键词
        if old_version:
            for v in self._versions.get(skill_id, []):
                if v.version == old_version:
                    for kw in v.sop.trigger.get("keywords", []):
                        self._keyword_index[kw].discard(skill_id)
                    break
        # 添加新版本
        for kw in new_sop.trigger.get("keywords", []):
            self._keyword_index[kw].add(skill_id)

    async def _gc_if_idle(self) -> None:
        if self._gc_lock.locked():
            return
        async with self._gc_lock:
            removed = 0
            to_remove = [v for v in self._pending_gc if v.ref_count <= 0 and v.deprecated]
            for v in to_remove:
                self._pending_gc.remove(v)
                removed += 1
            if removed:
                logger.debug(f"GC cleaned {removed} Skill versions")


class SkillContext:
    """请求级 Skill 版本绑定。"""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry
        self._versions: dict[str, SkillVersion] = {}

    def get_sop(self, skill_id: str) -> SkillSOP | None:
        ver = self._versions.get(skill_id)
        return ver.sop if ver else None

    def get_sops(self) -> list[SkillSOP]:
        return [v.sop for v in self._versions.values()]

    def release_all(self) -> None:
        for v in self._versions.values():
            self.registry.release(v)
        self._versions.clear()


# 全局单例
_global_registry: SkillRegistry | None = None
_initialized: bool = False


async def get_skill_registry() -> SkillRegistry:
    """获取全局 SkillRegistry（首次调用自动扫描 skills/）。"""
    global _global_registry, _initialized
    if _global_registry is None:
        _global_registry = SkillRegistry()
    if not _initialized:
        count = await _global_registry.scan_directory("skills/")
        _initialized = True
        if count > 0:
            logger.info(f"SkillRegistry initialized with {count} skills")
    return _global_registry
