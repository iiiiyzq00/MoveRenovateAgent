"""
Gunicorn 生产配置 — MoveRenovateAgent API.

启动: gunicorn -c gunicorn.conf.py app.main:app
"""

import multiprocessing
import os

# 绑定
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# Worker
workers = int(os.environ.get("WORKERS", min(4, multiprocessing.cpu_count() * 2 + 1)))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000

# 超时
timeout = 120
graceful_timeout = 30
keepalive = 5

# 日志
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sμs'

# 进程命名
proc_name = "mra_api"

# 优雅重启
max_requests = 10000
max_requests_jitter = 1000
