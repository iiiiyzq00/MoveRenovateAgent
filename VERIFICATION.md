# MoveRenovateAgent 验收指南

按逐字稿六大模块逐一验证，确保所有功能正确运行。

## 前置条件

```bash
cd /home/iisc/桌面/Agent/MoveRenovateAgent

# 1. 确保 .env 中的 API Key 有效
cat .env | grep -E 'LLM_API_KEY|AMAP_API_KEY'

# 2. 启动依赖服务（如已启动可跳过）
docker compose up -d postgres redis chromadb

# 3. 初始化知识库（首次）
python scripts/load_knowledge.py --reset

# 4. 启动后端（端口 8003）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8003 &

# 5. 启动前端（可选，新终端）
cd frontend && npm install && npm run dev &
```

---

## 一、ReAct Agent 自主工具调用（逐字稿第四块）

```bash
curl -s -X POST http://localhost:8003/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"从北京朝阳搬到海淀，三居室，有猫，算运费","stream":false}' | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('意图:', d.get('intent'))
print('Skill:', d.get('active_skills'))
mv = d.get('moving_summary',{})
print(f'搬家: {mv.get(\"volume_m3\")}m³, {mv.get(\"vehicle\")}, ¥{mv.get(\"freight_yuan\")}, {mv.get(\"distance_km\")}km')
tcl = d.get('tool_call_log',[])
print(f'工具调用: {len(tcl)} 次')
for t in tcl:
    print(f'  🔧 {t[\"tool\"]}')
print(f'方案长度: {len(d.get(\"response\",\"\"))} 字')
"
```

**预期：**
- `intent` = `new_topic`
- `active_skills` 含 `pet_relocation`
- `tool_call_log` ≥ 2 条（LLM 自主调用了 amap + lalamove）
- `moving_summary` 含完整的体积/车型/运费/距离（从工具 JSON 提取，非文本正则）
- 响应 > 2000 字，自然语言 Markdown 格式

---

## 二、意图路由：知识问答 vs 方案生成（逐字稿第一块）

### 类型 A：纯知识问答

```bash
curl -s -X POST http://localhost:8003/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"阳台有很多盆栽绿植，搬家要注意什么","stream":false}' | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('Skill:', d.get('active_skills'))
print('搬家摘要:', d.get('moving_summary'))    # 应为 None
print('装修摘要:', d.get('renovation_summary')) # 应为 None
print('工具调用:', len(d.get('tool_call_log',[])), '次')
print('决策依据:', d.get('recent_traces'))
print('响应长度:', len(d.get('response','')), '字')
"
```

**预期：**
- `active_skills` = `['plant_moving']`
- `moving_summary` = `None`（不跑搬家方案）
- `renovation_summary` = `None`（不跑装修方案）
- `recent_traces` 包含 Skill 来源标注
- 响应为纯绿植知识回答，无路线/运费/预算

### 类型 B：搬家方案

```bash
curl -s -X POST http://localhost:8003/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"从朝阳搬到海淀，算运费","stream":false}' | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('搬家:', d.get('moving_summary'))
print('装修:', d.get('renovation_summary'))
"
```

**预期：**
- `moving_summary` 非空（有体积/车型/运费/距离）
- `renovation_summary` = `None`（不提装修）

### 类型 D：完整搬装方案

```bash
curl -s -X POST http://localhost:8003/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"三居室90平，预算15万，朝阳到海淀，北欧风","stream":false}' | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('搬家:', d.get('moving_summary'))
print('装修:', d.get('renovation_summary'))
print('工具调用:', len(d.get('tool_call_log',[])), '次')
"
```

**预期：**
- `moving_summary` 和 `renovation_summary` 都不为 None
- `renovation_summary` 含 budget_yuan / total_days / materials_count
- 响应含 `---` 分隔的搬家+装修两部分

---

## 三、双 Agent 并行协同（逐字稿第一块）

```bash
python3 -c "
from app.agent.graph import build_graph, _needs_moving_plan, _needs_renovation_plan
from app.agent.state import create_empty_state

graph = build_graph()
nodes = list(graph.nodes.keys())
assert 'node_parallel_agents' in nodes, '缺少并行协调节点'
print(f'✅ Graph: {len(nodes)} 节点')

# 验证 asyncio.gather 并行架构（非 Send API）
import inspect
src = inspect.getsource(graph.nodes['node_parallel_agents'])
assert 'asyncio.gather' in src, '应使用 asyncio.gather 而非 Send API'
assert 'node_react_agent' in src and 'node_renovation_agent' in src
print('✅ 使用 asyncio.gather 并行调用双 Agent')

# 验证意图路由
s = create_empty_state(session_id='t', user_id='u')
assert not _needs_moving_plan('绿植怎么搬', s)
assert _needs_moving_plan('朝阳到海淀', s)
assert _needs_renovation_plan('90平15万', s)
print('✅ 意图路由正确：绿植→no, 地址→moving, 预算→reno')
"
```

**预期：**
- Graph 含 `node_parallel_agents` 节点
- 使用 `asyncio.gather` 并行（非 Send API）
- 意图路由：纯知识问答不触发搬家/装修

---

## 四、Skill 语义匹配（逐字稿第二块）

```bash
# 测试 1: 关键词精确匹配
curl -s -X POST http://localhost:8003/api/chat \
  -d '{"message":"有猫有钢琴","stream":false}' | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('Skill:', d.get('active_skills'))
"

# 测试 2: 语义兜底（非精确词）
curl -s -X POST http://localhost:8003/api/chat \
  -d '{"message":"家里毛孩子怎么安全运送","stream":false}' | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('Skill:', d.get('active_skills'))
"

# 测试 3: 负例过滤
curl -s -X POST http://localhost:8003/api/chat \
  -d '{"message":"我属狗","stream":false}' | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('Skill:', d.get('active_skills'))
"
```

**预期：**
- 测试 1: `['heavy_lifting', 'pet_relocation']`（精确 2 个，无误激活）
- 测试 2: `['pet_relocation']`（语义兜底命中）
- 测试 3: `[]` 或不含 `pet_relocation`（负例拦截）

---

## 五、Skill 约束 → 工具参数注入（逐字稿第二块）

```bash
python3 -c "
from app.agent.nodes.react_agent import ReActAgentNode
node = ReActAgentNode()

# 模拟宠物搬运 Skill 的工具参数模板
mock = '[宠物搬运] tool:lalamove_estimate → has_pet=True, vehicle_type=pet_friendly\n'
mock += '[宠物搬运] tool:amap_direction → strategy=shortest_time'
result = node._extract_skill_tool_params(mock)
assert 'lalamove_estimate' in result
assert 'amap_direction' in result
assert 'has_pet=True' in result
assert 'strategy=shortest_time' in result
print('✅ Skill tool params table:')
print(result)
"
```

**预期：**
- 生成 Markdown 表格，列出每个 Skill 对应的工具参数覆盖
- 表格注入 System Prompt 后 LLM 能正确使用

---

## 六、RAG 知识引用溯源（逐字稿第五块+第六块）

```bash
curl -s -X POST http://localhost:8003/api/chat \
  -d '{"message":"90平装修要注意水电和防水规范","stream":false}' | python3 -c "
import sys,json,re
d=json.load(sys.stdin)
resp = d.get('response','')

# 检查知识引用
has_section = '参考依据' in resp or '知识库' in resp or '溯源' in resp
print(f'📚 知识引用段: {\"✅\" if has_section else \"❌\"}')

# 检查国家标准编号
refs = re.findall(r'GB\s*\d+[.\d]*[-\d]*|JGJ\s*\d+[.\d]*[-\d]*', resp)
if refs:
    print(f'📗 引用标准: {refs[:3]}')

# 检查决策依据
traces = d.get('recent_traces', [])
print(f'📋 决策依据: {len(traces)} 条')
for t in traces:
    print(f'   {t[:80]}')
"
```

**预期：**
- 响应含「📚 参考依据（知识库溯源）」段
- 施工阶段匹配对应规范文档
- 引用标准编号（GB/JGJ）自动提取
- `recent_traces` 含规范来源

---

## 七、情景记忆（逐字稿第三块）

```bash
TEST_USER="00000000-0000-0000-0000-000000000001"

# 1. 生成方案
curl -s -X POST http://localhost:8003/api/chat \
  -d "{\"user_id\":\"$TEST_USER\",\"message\":\"三居室90平，预算15万，朝阳到海淀，北欧风\",\"stream\":false}" > /dev/null

sleep 3  # 等待异步保存

# 2. 查看历史方案
echo "=== 历史方案列表 ==="
curl -s "http://localhost:8003/api/plans?user_id=$TEST_USER" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f'共 {d[\"total\"]} 条方案')
for p in d['plans']:
    print(f'  v{p[\"plan_version\"]}: {p[\"summary\"][:80]}')
"

# 3. 新会话自动加载历史方案
echo "=== 新会话（自动加载上次方案） ==="
curl -s -X POST http://localhost:8003/api/chat \
  -d "{\"user_id\":\"$TEST_USER\",\"session_id\":null,\"message\":\"预算提到18万\",\"stream\":false}" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('意图:', d.get('intent'))  # 应为 incremental_update
mv=d.get('moving_summary',{})
rn=d.get('renovation_summary',{})
print(f'搬家: {mv.get(\"volume_m3\")}m³')
print(f'装修预算: ¥{rn.get(\"budget_yuan\",0):,.0f}')
"
```

**预期：**
- `GET /api/plans` 返回 ≥ 1 条方案
- 方案摘要含搬出/搬入地址和预算
- 新会话的 `intent` 为 `incremental_update`
- 搬家体积和装修预算从历史方案继承

---

## 八、用户画像与个性化（逐字稿第三块）

```bash
# 1. 画像保存（带偏好信号的对话）
curl -s -X POST http://localhost:8003/api/chat \
  -d '{"user_id":"00000000-0000-0000-0000-000000000002","message":"我喜欢简约风格，预算比较紧，有小孩，要环保材料","stream":false}"' > /dev/null

sleep 2

# 2. 验证 PG 写入
python3 -c "
import asyncio
async def check():
    from app.agent.nodes.profile import load_user_profile, _profile_to_text
    profile = await load_user_profile('00000000-0000-0000-0000-000000000002')
    print(f'PG 画像: {list(profile.keys()) if profile else \"(空)\"}')
    if profile:
        text = _profile_to_text(profile)
        print(f'向量化文本: {text}')
asyncio.run(check())
"

# 3. 验证 ChromaDB 向量写入
python3 -c "
import asyncio
async def check():
    from app.rag.chroma_client import KnowledgeBaseManager
    manager = KnowledgeBaseManager()
    col = manager.get_or_create_collection()
    results = col.get(where={'category': 'user_profile'})
    print(f'ChromaDB 画像文档: {len(results.get(\"ids\",[]))} 条')
    for doc in results.get('documents', []):
        print(f'  {doc[:100]}')
asyncio.run(check())
"
```

**预期：**
- PG 中保存了 budget_sensitivity/style/family_structure/eco_priority
- ChromaDB 中存在对应向量文档
- `_profile_to_text()` 生成自然语言描述

---

## 九、用户反馈热更新（逐字稿第五块）

```bash
# 提交纠正
curl -s -X POST http://localhost:8003/api/feedback \
  -d '{"session_id":"verify","target_type":"rag_document","target_id":"seed_doc_0000","rating":"correction","note":"冰箱搬运后需静置6小时而非2-4小时"}' | python3 -m json.tool

# 评分
curl -s -X POST http://localhost:8003/api/feedback \
  -d '{"session_id":"verify","target_type":"rag_document","target_id":"seed_doc_0000","rating":"helpful"}' | python3 -m json.tool

# 验证 ChromaDB 即时写入
python3 -c "
import asyncio
async def check():
    from app.rag.chroma_client import KnowledgeBaseManager
    manager = KnowledgeBaseManager()
    col = manager.get_or_create_collection()
    results = col.get(where={'source': 'user_feedback'})
    print(f'用户反馈文档: {len(results.get(\"ids\",[]))} 条')
    for doc in results.get('documents', []):
        print(f'  {doc[:120]}')
asyncio.run(check())
"
```

**预期：**
- `action` = `pending_review`（纠正）/ `score_updated`（评分）
- ChromaDB 即时写入用户纠正文档（无需重启）

---

## 十、前端验收

浏览器访问以下页面：

| URL | 页面 | 检查项 |
|-----|------|--------|
| `http://localhost:3000/` | 聊天 | 毛玻璃导航栏、快速示例按钮、消息气泡、三点跳动加载、圆发送按钮 |
| `http://localhost:3000/login` | 登录 | 毛玻璃卡片、渐变标题、图标前缀输入框、hover 动效 |
| `http://localhost:3000/plans` | 历史方案 | 毛玻璃卡片网格、hover 平移+左侧渐变条、相对时间显示 |
| `http://localhost:3000/plans/:id` | 方案详情 | 双栏布局、预算进度条四色渐变、物品标签云、恢复按钮 |

---

## 十一、自动化测试

```bash
# 单元测试
python3 -m pytest tests/unit/ -v --tb=short 2>&1 | tail -10

# 集成测试（需要 LLM API Key）
python3 -m pytest tests/integration/ -v --tb=short 2>&1 | tail -10
```

**预期：** 124+ 单元测试通过

---

## 十二、端到端演示

```bash
bash demo_script.sh http://localhost:8003
```

**预期输出（9 步场景）：**

| 步骤 | 场景 | 预期 |
|------|------|------|
| 1 | 健康检查 | `{"status":"ok"}` |
| 2 | 搬家+装修（有猫+北欧风） | intent=new_topic, skill=pet_relocation, 🚛+🏗️ 双方案, 甘特图 ✅ |
| 3 | 增量修改（预算+钢琴） | intent=incremental_update, skill=heavy_lifting, 预算 15→18万 |
| 4 | 适老化改造 | intent=incremental_update, skill=elderly_accessible, 材料 12→16 |
| 5 | 绿植搬运 | intent=new_topic, skill=plant_moving, 搬家/装修=None（纯知识）, 甘特图 N/A |
| 6 | 多场景联合（宠物+绿植+老人） | intent=new_topic, 3 个 Skill 同时激活 |
| 7 | Skill 热加载 | 4 skills, 无需重启 |
| 8 | 用户反馈 | action=score_updated |
| 9 | Prometheus 指标 | http_requests_total ≥ 9 |

---

## 离线检查（无需启动服务）

```bash
python3 -c "
from app.agent.graph import build_graph, node_parallel_agents
from app.agent.nodes.react_agent import ReActAgentNode
from app.agent.nodes.profile import _profile_to_text
from app.agent.nodes.renovation_agent import RenovationAgentNode
from app.mcp.tool_wrappers import get_all_tools
from app.memory.plan_store import save_plan, load_latest_plan
from app.agent.nodes.skill_activate import SkillActivateNode

# Graph
g = build_graph()
assert 'node_parallel_agents' in g.nodes
print(f'✅ Graph: {len(g.nodes)} nodes')

# Tools
tools = get_all_tools()
assert len(tools) == 3
print(f'✅ Tools: {[t.name for t in tools]}')

# Profile
text = _profile_to_text({'budget_sensitivity':{'value':'tight'},'style':{'value':'北欧'}})
assert '预算紧张' in text
print(f'✅ Profile text: {text}')

# RAG citations
n = RenovationAgentNode()
cites = n._build_rag_citations(
    {'retrieved_docs':[{'content':'GB 50327-2001 防水规范','score':0.95,'tags':['防水'],'authority_level':'mandatory_standard','source':'standard'}]},
    [{'phase':'泥瓦施工'}]
)
assert len(cites) > 0
print(f'✅ RAG cite: {cites[0][\"authority\"]} {cites[0][\"source_ref\"]}')

# Skill semantic
assert hasattr(SkillActivateNode, '_semantic_match')
print('✅ Skill semantic match available')

# ReAct agent
rn = ReActAgentNode()
assert len(rn.tools) == 3
print(f'✅ ReActAgentNode: {len(rn.tools)} tools')

print()
print('🎉 全部离线检查通过！')
"
```
