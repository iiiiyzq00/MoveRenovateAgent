# MoveRenovateAgent 部署指南

## 环境要求

| 组件 | 最低版本 | 说明 |
|------|----------|------|
| Python | 3.10+ | 后端 |
| Node.js | 18+ | 前端 |
| Docker | 24+ | 容器化部署 |
| Docker Compose | v2 | 多服务编排 |
| PostgreSQL | 16 | 持久化（画像/方案/溯源） |
| Redis | 7 | 会话缓存 |
| ChromaDB | 0.5+ | 向量知识库 |

## Docker Compose 部署（推荐）

```bash
# 1. 配置环境变量
cp .env.example .env
vim .env  # 填入 LLM_API_KEY（必填）、AMAP_API_KEY（可选）

# 2. 启动所有服务
docker compose up -d

# 3. 验证
curl http://localhost:8000/health
```

### 服务列表

| 服务 | 端口 | 说明 |
|------|------|------|
| api | 8000 | FastAPI 后端 |
| frontend | 3001 | Vue 3 前端（生产构建） |
| postgres | 5432 | PostgreSQL 数据库 |
| redis | 6379 | Redis 缓存 |
| chromadb | 8001 | ChromaDB 向量库 |
| init_kb | - | 知识库初始化（一次性） |

## 手动部署

### 1. 依赖服务

```bash
# 启动 PostgreSQL + Redis + ChromaDB
docker compose up -d postgres redis chromadb
```

### 2. 数据库初始化

```bash
# 创建表结构
psql -h localhost -U mra -d move_renovate -f scripts/init_db.sql

# 初始化知识库（25 条预置文档）
python scripts/load_knowledge.py --reset
```

### 3. 后端

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 开发模式
uvicorn app.main:app --reload --host 0.0.0.0 --port 8003

# 生产模式（Gunicorn + Uvicorn workers）
gunicorn -c gunicorn.conf.py app.main:app
```

### 4. 前端

```bash
cd frontend
npm install
npm run build   # 生产构建 → dist/
npm run preview # 预览生产构建
```

开发模式（HMR 热更新）：
```bash
npm run dev     # → http://localhost:3000
```

## 配置说明

### 环境变量（.env）

```bash
# ── 必配 ──
LLM_PROVIDER=deepseek           # openai | deepseek | anthropic
LLM_API_KEY=sk-your-key         # LLM API Key

# ── 可选 ──
AMAP_API_KEY=your-amap-key      # 高德地图 API Key（无 Key 使用 Mock）
LALAMOVE_API_KEY=               # 货拉拉 API Key（企业认证）
LALAMOVE_API_SECRET=            # 货拉拉 HMAC 签名密钥
PORT=8003                       # 服务端口

# ── Embedding ──
EMBEDDING_PROVIDER=openai       # openai | local_bge | chroma_default
EMBEDDING_API_KEY=sk-your-key

# ── 数据库 ──
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=mra
POSTGRES_PASSWORD=mra_secret
POSTGRES_DB=move_renovate

REDIS_HOST=localhost
REDIS_PORT=6379

CHROMADB_HOST=localhost
CHROMADB_PORT=8001
```

### Gunicorn 配置（gunicorn.conf.py）

```python
bind = "0.0.0.0:8003"
workers = 4                    # 推荐: CPU 核心数 × 2 + 1
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 120                  # LLM 调用可能较长
```

## 备份与恢复

### PostgreSQL

```bash
# 备份
pg_dump -h localhost -U mra move_renovate > backup_$(date +%Y%m%d).sql

# 恢复
psql -h localhost -U mra move_renovate < backup_20260101.sql
```

### ChromaDB

```bash
# ChromaDB 使用本地持久化目录
tar -czf chroma_backup.tar.gz ./chroma_data/
```

## 监控

### Prometheus 指标

```
GET /metrics
```

| 指标 | 类型 | 说明 |
|------|------|------|
| http_requests_total | counter | HTTP 请求总数 |
| active_sessions | gauge | 当前活跃会话数 |
| llm_call_duration_seconds | summary | LLM 调用耗时 |

### JSON 日志

所有日志以 JSON 格式输出到 stdout，包含：`ts`、`level`、`logger`、`msg`、`req_id`（如有）、`session`（如有）。

```bash
# 查看 React Agent 工具调用日志
docker compose logs api | grep '\[react\]'

# 查看 Skill 激活日志
docker compose logs api | grep '\[skill\]'
```

## 故障排除

### API Key 无效（401）
```
Error: Authentication Fails, Your api key is invalid
```
→ 检查 `.env` 中的 `LLM_API_KEY` 是否正确。

### 端口已占用（Errno 98）
```
ERROR: [Errno 98] Address already in use
```
→ 修改 `.env` 中的 `PORT` 或使用其他端口：
```bash
uvicorn app.main:app --port 8003
```

### ChromaDB 连接失败
```
ChromaDB HTTP connection failed
```
→ 自动降级到本地持久化模式（`./chroma_data/`），功能不受影响。

### Redis 不可用
```
Redis unavailable, using in-memory fallback
```
→ 自动降级到内存存储（⚠️ 仅限单 worker，多 worker 需 Redis）。

### 货拉拉 API 未配置
```
Lalamove API key/secret not configured, using mock
```
→ 正常行为，系统自动使用内置价格表模拟，功能可用。

### LangGraph 并行冲突
```
INVALID_CONCURRENT_GRAPH_UPDATE
```
→ 当前版本已使用 asyncio.gather 替代 Send API，不会出现此问题。
