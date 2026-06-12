"""
应用配置模块 — 使用 pydantic-settings 管理所有环境变量。

Usage:
    from app.config import get_config
    cfg = get_config()
    print(cfg.amap_api_key)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """应用全局配置，所有值均可通过环境变量 / .env 文件覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 应用基础 ──────────────────────────────────────────
    app_name: str = "MoveRenovateAgent"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ── 服务绑定 ──────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]

    # ── 数据库 ────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "mra"
    postgres_password: str = "mra_secret"
    postgres_db: str = "move_renovate"
    postgres_pool_min: int = 2
    postgres_pool_max: int = 20

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Redis ─────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    session_ttl_seconds: int = 7200  # 2 小时

    @property
    def redis_dsn(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ── ChromaDB ──────────────────────────────────────────
    chromadb_host: str = "localhost"
    chromadb_port: int = 8001
    chromadb_collection: str = "moving_renovation_kb"
    chromadb_persist_dir: str = "./chroma_data"

    # ── LLM ───────────────────────────────────────────────
    llm_provider: Literal["openai", "deepseek", "anthropic"] = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096

    # embedding 模型
    embedding_provider: Literal["openai", "local"] = "openai"
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # ── MCP 工具 ──────────────────────────────────────────
    amap_api_key: str = ""
    lalamove_api_key: str = ""       # 空 → 使用内置价格表模拟
    lalamove_api_secret: str = ""    # 货拉拉 HMAC 签名密钥

    mcp_timeout_seconds: float = 5.0
    mcp_max_retries: int = 2

    # ── Skill ─────────────────────────────────────────────
    skills_dir: str = "./skills"
    skill_watch_enabled: bool = True

    # ── 决策溯源 ──────────────────────────────────────────
    max_recent_traces: int = 5
    max_trace_ids: int = 20

    # ── 画像 ──────────────────────────────────────────────
    profile_extraction_enabled: bool = True


@lru_cache()
def get_config() -> AppConfig:
    """获取配置单例（进程级缓存）。"""
    return AppConfig()
