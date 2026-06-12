# MoveRenovateAgent — 智能搬装规划 Agent

基于 LLM 的对话式搬家+装修一体化规划助手。用户通过自然语言多轮对话，获得一份可随时增量调整的一体化方案：搬家物品清单、装箱方案、运费估算、装修预算分配、施工阶段甘特图、材料清单。

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + Gunicorn + Uvicorn |
| Agent 编排 | LangGraph（9 节点状态机 + asyncio.gather 并行） |
| LLM | DeepSeek / OpenAI / Anthropic（OpenAI-compatible API） |
| Agent 模式 | **ReAct** — LLM 自主推理 + 工具调用（amap / lalamove / rag_search） |
| 前端 | Vue 3 + TypeScript + Vite + Vue Router 4（毛玻璃 UI） |
| 短期记忆 | Redis 7（多 worker 安全，2h TTL） |
| 长期记忆 | PostgreSQL 16（用户画像 + 历史方案 + 决策溯源归档） |
| 向量知识库 | ChromaDB（25+ 预置文档 + 用户反馈热更新） |
| Embedding | OpenAI / BGE-large-zh / ChromaDB 默认（三选一） |
| 工具集成 | 高德地图驾车路线 v3 API（真实）、货拉拉运费（API 框架就绪 + Mock 降级） |
| 容器化 | Docker Compose（6 服务） |
| 监控 | Prometheus `/metrics` + JSON 日志 |

## 快速启动

### 一键部署（Docker Compose）

```bash
git clone <repo>
cd MoveRenovateAgent
cp .env.example .env
# 编辑 .env：至少填入 LLM_API_KEY
docker compose up -d
```

| 服务 | 地址 |
|------|------|
| 前端聊天界面 | http://localhost:3001 |
| 后端 API | http://localhost:8000 |
| API 文档 (Swagger) | http://localhost:8000/docs |
| Prometheus 指标 | http://localhost:8000/metrics |

### 本地开发

```bash
# 1. 环境变量
cp .env.example .env && vim .env

# 2. 启动依赖服务
docker compose up -d postgres redis chromadb

# 3. Python 环境
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4. 初始化知识库（首次）
python scripts/load_knowledge.py --reset

# 5. 启动 API（默认端口 8000，可通过 .env 修改）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8003

# 6. 启动前端（新终端）
cd frontend && npm install && npm run dev
# → http://localhost:3000
```

### 功能演示

```bash
bash demo_script.sh http://localhost:8003
```

自动发送 9 组预设对话，展示：搬家+装修方案、增量修改、适老化改造、绿植搬运、多场景联合、Skill 热加载、用户反馈、Prometheus 指标。

## 运行测试

```bash
pytest tests/ -v                          # 全部测试
pytest tests/unit/ -v                     # 仅单元测试
pytest tests/integration/ -v              # 仅集成测试
pytest tests/ -v --cov=app --cov-report=term-missing  # 覆盖率
```

## 项目结构

```
MoveRenovateAgent/
├── app/
│   ├── main.py                     # FastAPI 入口 + Prometheus /metrics + 健康检查
│   ├── config.py                   # Pydantic Settings（.env 管理）
│   ├── agent/
│   │   ├── state.py                # MasterState TypedDict（30+ 字段）
│   │   ├── graph.py                # build_graph() 9 节点状态机
│   │   ├── dependency_graph.py     # 声明式级联依赖图
│   │   ├── llm_client.py           # LLM 客户端（OpenAI-compat）
│   │   ├── nodes/                  # LangGraph 节点
│   │   │   ├── intent.py           #   意图分类（合并 RAG 预判 + 语义比较）
│   │   │   ├── skill_activate.py   #   Skill 激活（关键词 O(1) + 语义兜底）
│   │   │   ├── cascade.py          #   级联分析 + 并行协调
│   │   │   ├── rag_retrieve.py     #   RAG 自适应检索
│   │   │   ├── react_agent.py      #   ★ ReAct Agent（自主推理 + 工具调用）
│   │   │   ├── moving_agent.py     #   搬家 Agent（规则引擎，降级备用）
│   │   │   ├── renovation_agent.py #   装修 Agent（预算/施工/材料/甘特图/RAG引用）
│   │   │   └── profile.py          #   画像提炼 + PG 持久化 + ChromaDB 向量化
│   │   └── prompts/                # Agent System Prompt 模板
│   │       ├── react_agent.md      #   ReAct Agent 提示词（类型 A/B/C/D）
│   │       ├── moving_agent.md     #   搬家 Agent 提示词
│   │       ├── renovation_agent.md #   装修 Agent 提示词
│   │       └── intent_classify.md  #   意图分类提示词
│   ├── skill/
│   │   └── registry.py             # SkillRegistry（版本化 + RCU 并发安全）
│   ├── memory/
│   │   ├── session_store.py        # Redis 优先 + 内存降级（undo 快照栈）
│   │   └── plan_store.py           # ★ 历史方案存取（情景记忆）
│   ├── rag/
│   │   ├── chroma_client.py        # ChromaDB 连接 + KnowledgeBaseManager
│   │   ├── retriever.py            # AdaptiveRetriever（7类×动态阈值）
│   │   └── embedding.py            # OpenAI / BGE / ChromaDB 三选一 + 语义相似度
│   ├── mcp/
│   │   ├── client_manager.py       # MCP 统一调用（超时/重试/降级/缓存）
│   │   ├── tool_wrappers.py        # ★ LangChain Tool 封装（ReAct 可调用）
│   │   └── tools/
│   │       ├── mock_tools.py       #   本地模拟（距离矩阵+价格表）
│   │       └── lalamove_api.py     #   货拉拉真实 API（HMAC 签名）
│   ├── trace/
│   │   └── evidence_builder.py     # 决策溯源构建（工具/RAG/计算三类证据）
│   └── api/                        # REST 端点
│       ├── chat.py                 #   POST /api/chat（SSE 流式 + 非流式 + 方案自动保存）
│       ├── session.py              #   GET/POST /api/session/{id} + GET/POST /api/plans
│       ├── skill.py                #   POST /api/skill/reload
│       └── feedback.py             #   POST /api/feedback（→ ChromaDB 热更新）
├── skills/                         # 4 个热插拔 Skill（YAML SOP + embedding_threshold）
│   ├── pet_relocation/             #   宠物搬运（3 约束，2 工具参数覆盖）
│   ├── heavy_lifting/              #   大件吊装（2 约束）
│   ├── elderly_accessible/         #   适老化改造（6 约束）
│   ├── plant_moving/               #   绿植搬运（7 约束）
│   └── _template/                  #   新 Skill 模板
├── frontend/                       # Vue 3 前端（毛玻璃 UI）
│   └── src/
│       ├── App.vue                 #   导航栏 + 路由视图 + 全局样式变量
│       ├── main.ts                 #   入口（注册 Vue Router）
│       ├── router/index.ts         #   路由（/、/login、/plans、/plans/:id）
│       └── views/
│           ├── Chat.vue            #   聊天界面（SSE 流式 + Markdown + 决策溯源）
│           ├── Login.vue           #   用户登录/创建
│           ├── Plans.vue           #   历史方案列表
│           └── PlanDetail.vue      #   方案详情 + 恢复
├── scripts/
│   ├── init_db.sql                 # PostgreSQL 建表（含 historical_plans）
│   └── load_knowledge.py           # RAG 知识库初始化（25 条）
├── tests/
│   ├── unit/                       # 125+ 单元测试
│   ├── integration/                # 集成测试（Skill合并/反馈热更新）
│   └── e2e/                        # 端到端测试
├── docker-compose.yml              # 6 服务编排（api/frontend/postgres/redis/chromadb/init_kb）
├── gunicorn.conf.py                # Gunicorn 生产配置（4 workers）
├── demo_script.sh                  # 9 步自动化演示
├── DEPLOYMENT.md                   # 部署指南（SSL/备份/监控/故障排除）
├── VERIFICATION.md                 # 验收指南（逐功能验证步骤）
├── Dockerfile
├── requirements.txt
├── pyproject.toml
└── .env.example
```

## 架构

```
用户输入 → POST /api/chat
              │
              ▼
┌─────────────────────────────────────────────┐
│              LangGraph 状态机                │
│                                             │
│  intent_classify ──→ skill_activate         │
│        │                    │               │
│        ▼                    ▼               │
│  cascade_compute      rag_retrieve          │
│        │                    │               │
│        └─────────┬──────────┘               │
│                  ▼                          │
│         node_parallel_agents                │
│         ┌────────┴────────┐                 │
│         ▼                  ▼                │
│    react_agent      renovation_agent        │
│   (ReAct 循环)     (规则引擎+LLM)           │
│  自主调工具推理    预算/施工/材料/甘特图      │
│         │                  │                │
│         └────────┬─────────┘                │
│                  ▼                          │
│         合并 + 交叉感知 + 溯源               │
│                  │                          │
│                  ▼                          │
│           profile_extract                   │
│                  │                          │
│                  ▼                          │
│              echo (响应)                     │
└─────────────────────────────────────────────┘
              │
              ▼
        SSE 流式 / JSON 响应
              │
              ▼
    ┌─── Vue 3 前端（Markdown + Mermaid + 决策溯源 + 工具调用日志）
    │
    ├─── Redis ←→ Session 状态（跨 worker 共享）
    ├─── PostgreSQL ←→ 用户画像 / 历史方案 / 决策溯源归档
    ├─── ChromaDB ←→ 25+ 搬装知识文档 + 用户画像向量 + 反馈热更新
    ├─── 高德 API ←→ 实时路线（地理编码 → 驾车规划 v3）
    └─── 货拉拉 API ←→ 运费报价（Key 配置后自动切换，无 Key 降级 Mock）
```

### ReAct Agent 工具调用流程

```
用户: "三居室有猫，朝阳到海淀，算运费"
  │
  ▼
ReAct Agent (LLM 自主推理):
  Iteration 1: 💭 需要路线和宠物知识
               → 调用 amap_direction(朝阳→海淀, strategy=2)
               → 调用 rag_search("猫咪搬家注意事项")
  Iteration 2: 💭 路线有了 18km，现在算运费
               → 调用 lalamove_estimate(vol=22m³, has_pet=true)
  Iteration 3: 💭 信息齐全，输出方案
               → 自然语言 Markdown 方案
```

### 意图路由

```
用户输入
  │
  ▼
意图分类
  ├─ 纯知识问答（"绿植怎么搬"）→ 只跑 react_agent（rag_search）
  ├─ 搬家需求（"朝阳到海淀"）  → react_agent（amap + lalamove）
  ├─ 装修需求（"90平15万"）    → renovation_agent
  └─ 完整需求（地址+预算）     → 双 Agent 并行（asyncio.gather）
```

### 增量更新流程

```
"预算提到18万，再加一架钢琴"
  → intent_classify: incremental_update
  → cascade_compute: budget → 装修重算, piano → 搬家重算
  → skill_activate: heavy_lifting (钢琴关键词)
  → node_parallel_agents: 双 Agent 并行更新
  → merge: 合并搬家+装修响应
  → profile_extract: 预算敏感度→flexible
  → 自动保存到 historical_plans
```

## 已实现功能

- ✅ **ReAct Agent 自主工具调用**：LLM 自主决定调 amap/lalamove/rag_search，5 轮迭代推理
- ✅ **双 Agent 并行协同**：asyncio.gather 并行执行搬家+装修 Agent，共享状态，交叉感知协调
- ✅ **意图路由**：区分知识问答/搬家/装修/完整方案，按需运行 Agent
- ✅ **LLM 驱动规划**：DeepSeek/OpenAI 生成完整方案，自然语言输出
- ✅ **多轮增量更新**：智能识别 MODIFY/APPEND/DELETE，声明式依赖图自动计算级联影响
- ✅ **语义实体比较**：地址/物品名 embedding 相似度（"北京朝阳" ≈ "北京市朝阳区"→ 过滤）
- ✅ **4 个热插拔 Skill**：宠物搬运/大件吊装/适老化改造/绿植搬运，YAML 声明式 SOP
- ✅ **Skill 关键词+语义双路径激活**：O(1) 倒排索引 + embedding 语义兜底（纯知识问答自动匹配）
- ✅ **Skill 工具参数注入**：激活 Skill 的参数模板自动注入 System Prompt，LLM 调用工具时使用
- ✅ **高德地图实时路线**：地理编码（地址→坐标）→ 驾车路线 v3 API → 真实距离/时长/路费
- ✅ **货拉拉运费**：真实 API 框架就绪（HMAC 签名），无 Key 时透明降级内置价格表
- ✅ **RAG 知识库**：25 条预置文档，ChromaDB 向量检索，自适应阈值+topK 放宽
- ✅ **装修 RAG 知识引用**：施工阶段自动匹配规范文档，标注 GB/JGJ 编号溯源
- ✅ **中文 Embedding 三选一**：OpenAI / BGE-large-zh / ChromaDB 默认，动态阈值适配
- ✅ **用户反馈热更新**：纠正内容即时写入 ChromaDB，贝叶斯评分更新文档权重
- ✅ **决策溯源**：EvidenceBuilder 生成工具/RAG/Skill 三类证据，Markdown 折叠展示
- ✅ **用户画像**：信号词+规则引擎+LLM 三重提炼，PG 持久化 + ChromaDB 向量化双写
- ✅ **情景记忆**：历史方案自动保存到 PG，跨会话加载最近方案，支持"继续上次的"修改
- ✅ **Session 管理**：Redis 优先（多 worker 安全）+ 内存降级，2h TTL，5 层快照栈支持 /undo
- ✅ **Vue 3 毛玻璃前端**：4 页面（聊天/登录/历史方案/方案详情），SSE 流式，Markdown+Mermaid，工具调用日志
- ✅ **Prometheus 监控**：/metrics 端点（http_requests_total, active_sessions, llm_call_duration_seconds）
- ✅ **Docker Compose 一键部署**：6 服务（frontend/api/postgres/redis/chromadb/init_kb），健康检查依赖链

## 配置指南

### 必配环境变量

```bash
# .env
LLM_PROVIDER=deepseek              # openai / deepseek / anthropic
LLM_API_KEY=sk-your-key            # LLM API Key
AMAP_API_KEY=your-amap-key         # 高德地图 Web API Key（可选，无 Key 使用本地 Mock）
PORT=8003                          # 服务端口（默认 8000，冲突时修改）
```

### Embedding 模型选择

```bash
# 方案 A：ChromaDB 默认（无需配置，英文 all-MiniLM-L6-v2，中文精度低）
EMBEDDING_PROVIDER=chroma_default

# 方案 B：本地 BGE 中文模型（推荐，首次下载 ~1.3GB，1024维）
EMBEDDING_PROVIDER=local_bge
pip install sentence-transformers

# 方案 C：OpenAI text-embedding-3-small（需 API Key，中文精度最高，1536维）
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=sk-your-key
```

### RAG 知识库初始化

```bash
# 首次加载 25 条预置文档
python scripts/load_knowledge.py --reset

# 切换 Embedding Provider 后需重新索引
EMBEDDING_PROVIDER=local_bge python scripts/load_knowledge.py --reset
```

### 如何添加新 Skill

1. 复制模板：`cp -r skills/_template skills/your_scene/`
2. 编辑 `skill.yaml`：
   - `skill_id`：唯一标识
   - `trigger.keywords`：激活关键词列表
   - `trigger.trigger_examples`：语义匹配示例
   - `trigger.embedding_threshold`：语义匹配阈值（推荐 0.65）
   - `trigger.negative_examples`：负例（避免误激活）
   - `constraints`：约束规则（severity: mandatory/warning/conditional）
   - `tools[].params_template`：工具参数覆盖（自动注入 Agent 调用）
   - `output.must_include`：输出必须包含的内容
   - `test_cases`：自动化测试用例
3. 热加载（无需重启）：`curl -X POST http://localhost:8003/api/skill/reload`
4. 验证：发送含触发关键词的消息

### 用户反馈

```bash
# 纠正知识库（即时生效）
curl -X POST http://localhost:8003/api/feedback \
  -d '{"session_id":"s1","target_type":"rag_document","target_id":"kb_0000","rating":"correction","note":"冰箱搬运后静置6小时"}'

# 评分（更新文档权重）
curl -X POST http://localhost:8003/api/feedback \
  -d '{"session_id":"s1","target_type":"rag_document","target_id":"kb_0000","rating":"helpful"}'
```

### 历史方案 API

```bash
# 列出用户方案
curl "http://localhost:8003/api/plans?user_id=xxx"

# 获取方案详情
curl "http://localhost:8003/api/plans/{plan_id}"

# 恢复方案到新会话
curl -X POST "http://localhost:8003/api/plans/{plan_id}/restore"
```

## 验收清单

| 功能 | 验证方式 | 预期 |
|------|----------|------|
| 搬家方案 | `curl POST /api/chat -d '{"message":"三居室，朝阳到海淀"}'` | 路线+车型+运费 |
| 装修方案 | `curl POST /api/chat -d '{"message":"90平装修15万北欧风"}'` | 预算分配+甘特图+材料清单 |
| ReAct 工具调用 | 查看 `tool_call_log` | ≥1 次工具调用（amap/lalamove/rag） |
| 知识问答 | `curl POST /api/chat -d '{"message":"绿植怎么搬"}'` | 纯知识回答，无搬家/装修方案 |
| 增量修改 | 同一 session 第二轮"预算提到18万" | intent=incremental_update，级联重算 |
| Skill 激活 | "有猫"→pet_relocation，"有老人"→elderly_accessible | active_skills 含对应 Skill |
| 语义兜底 | "毛孩子怎么运"（非精确词） | pet_relocation 激活 |
| 负例过滤 | "我属狗" | pet_relocation 不激活 |
| 适老化改造 | "有老人要适老化" | 硬装60%，材料含防滑地砖+扶手 |
| SSE 流式 | 前端输入消息 | 意图→方案逐步显示 |
| 跨会话画像 | 同一 user_id 两次对话 | 偏好自动加载 |
| 历史方案 | 同 user_id 新会话 | 自动加载上次方案的地址/预算 |
| 用户反馈 | `POST /api/feedback` correction | ChromaDB 即时写入可检索 |
| Skill 热加载 | `POST /api/skill/reload` | 4 skills, 无需重启 |
| Prometheus | `GET /metrics` | http_requests_total 等指标 |
| 健康检查 | `GET /health` | status=ok |
| Docker 部署 | `docker compose up -d` | 6 服务全部 healthy |

## 部署与运维

详见 [DEPLOYMENT.md](DEPLOYMENT.md)：SSL 配置、PostgreSQL/ChromaDB 备份、Gunicorn 多 worker、Prometheus 指标说明、故障排除。

## 验收测试

详见 [VERIFICATION.md](VERIFICATION.md)：逐功能验证步骤和 curl 命令。
