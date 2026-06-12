"""
FastAPI 应用入口 — 含 Prometheus 指标 + JSON 日志。

启动:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    gunicorn -c gunicorn.conf.py app.main:app  # 生产
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_config

# ── JSON 日志配置 ────────────────────────────────────────
class _SafeJsonFormatter(logging.Formatter):
    """安全的 JSON 日志格式器，处理缺失的日志字段。"""
    def format(self, record):
        data = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 可选字段（仅存在时添加）
        for attr in ("req_id", "session"):
            val = getattr(record, attr, None)
            if val:
                data[attr] = val
        return json.dumps(data, ensure_ascii=False)

LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": _SafeJsonFormatter}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"level": "INFO", "handlers": ["console"]},
}
try:
    logging.config.dictConfig(LOG_CONFIG)
except Exception:
    pass  # 保持简单

logger = logging.getLogger(__name__)

# ── Prometheus 指标 ──────────────────────────────────────
_metrics: dict[str, int | float] = {
    "http_requests_total": 0,
    "active_sessions": 0,
    "llm_call_duration_seconds_sum": 0.0,
    "llm_call_count": 0,
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    cfg = get_config()
    logger.info(f"{cfg.app_name} v{cfg.app_version} starting")
    logger.info(f"LLM: {cfg.llm_provider}/{cfg.llm_model} | Embedding: {cfg.embedding_provider}")
    yield
    logger.info(f"{cfg.app_name} shutting down")


class MetricsMiddleware(BaseHTTPMiddleware):
    """Prometheus 指标中间件。"""
    async def dispatch(self, request: Request, call_next):
        _metrics["http_requests_total"] += 1
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start
        if request.url.path.startswith("/api/chat"):
            _metrics["llm_call_duration_seconds_sum"] += duration
            _metrics["llm_call_count"] += 1
        # 注入 request_id
        if "X-Request-ID" not in response.headers:
            response.headers["X-Request-ID"] = str(uuid.uuid4())[:8]
        return response


def create_app() -> FastAPI:
    cfg = get_config()
    app = FastAPI(
        title=cfg.app_name, version=cfg.app_version,
        description="智能搬装规划 Agent — 搬家+装修一体化规划助手",
        lifespan=lifespan,
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins,
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    from app.api.chat import router as chat_router
    from app.api.session import router as session_router
    from app.api.skill import router as skill_router
    from app.api.feedback import router as feedback_router
    app.include_router(chat_router, prefix="/api")
    app.include_router(session_router, prefix="/api")
    app.include_router(skill_router, prefix="/api")
    app.include_router(feedback_router, prefix="/api")
    return app


app = create_app()


# ── Prometheus /metrics 端点 ──────────────────────────────
@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点。"""
    lines = [
        "# HELP http_requests_total Total HTTP requests",
        "# TYPE http_requests_total counter",
        f"http_requests_total {int(_metrics['http_requests_total'])}",
        "# HELP active_sessions Currently active sessions",
        "# TYPE active_sessions gauge",
        f"active_sessions {int(_metrics['active_sessions'])}",
        "# HELP llm_call_duration_seconds LLM call duration",
        "# TYPE llm_call_duration_seconds summary",
    ]
    if _metrics["llm_call_count"] > 0:
        avg = _metrics["llm_call_duration_seconds_sum"] / _metrics["llm_call_count"]
        lines.append(f"llm_call_duration_seconds{{quantile=\"0.5\"}} {avg:.3f}")
        lines.append(f"llm_call_duration_seconds_count {int(_metrics['llm_call_count'])}")
        lines.append(f"llm_call_duration_seconds_sum {_metrics['llm_call_duration_seconds_sum']:.3f}")
    return Response("\n".join(lines) + "\n", media_type="text/plain")


@app.get("/health")
async def health_check():
    return {"status": "ok", "app": get_config().app_name, "version": get_config().app_version}


@app.get("/api/health")
async def api_health_check():
    return {"status": "ok"}

