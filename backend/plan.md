# DeepSeek 风格 AI 对话平台 — 后端实现计划

## Context

在 `D:\study\llm`(现为空目录,已建好空的 `backend/`、`web/` 两个子目录)从零实现一个前后端分离的 AI 在线对话平台后端,对标 DeepSeek 网页版。技术栈:FastAPI + langchain + deepagents + PostgreSQL + Docker。核心功能:一键切换模型(多平台统一)、历史对话、流式输出、图片输出(agent 代码绘图)。

已确认的决策:
- **模型切换**:通过 OpenAI 兼容协议统一接入多平台(DeepSeek/OpenAI/通义/智谱/豆包等),后端动态返回模型列表,请求带 model 参数切换
- **图片输出**:agent 内置代码执行工具,用 matplotlib 绘图返回 PNG URL(DeepSeek 无原生图片生成能力,已核实)
- **历史存储**:PostgreSQL;会话/消息表 + LangGraph checkpointer(独立连接)
- **认证**:暂不做登录,JWT 后续再议

环境:Windows 11、Python 3.13.5、Docker 29.6.1、Git Bash。**本阶段只做后端**;`web/` 留空,但 API 契约按前端分离设计(SSE 事件协议、CORS 放开)。

## 关键技术决策(已核实)

| 决策点 | 方案 | 原因 |
|---|---|---|
| agent 包 | `deepagents==0.6.12`(PyPI 发行名,**不是** `langchain-deepagents`;0.7.x 有 breaking change,锁版本),`from deepagents import create_deep_agent` | 已核实 PyPI 现状 |
| 模型切换 | **按 model 缓存 agent 实例**(AgentManager LRU,上限 32);`create_deep_agent(model=..., checkpointer=saver)` 直接支持 checkpointer | 会话状态在 checkpointer(thread_id=conversation_id)里,换模型上下文天然连续;不用 middleware 换模型(依赖内部契约,难排查) |
| SSE | 原生 `StreamingResponse` + 手写 SSE 帧(约 5 行 helper) | 单向推送足够,零依赖 |
| checkpointer | `langgraph-checkpoint-postgres>=3.1.1`,`AsyncPostgresSaver`(psycopg3),与 SQLAlchemy asyncpg 完全独立的连接池 | 3.x 新 API,需手动 `setup()` |
| 代码执行 | 自研 `run_python_code` 工具:subprocess + 60s 超时 + env 裁剪(去掉 API_KEY)+ 输出截断 8KB + 信号量限流 | 默认 execute 工具是进程内任意代码,风险不可控;M3 实测确认默认工具能否禁用 |
| 双轨记忆 | checkpointer 是 agent 上下文唯一来源;DB 两张表只服务 UI;checkpointer 空时从 DB 回放最近 50 条兜底 | 任一被清都能自愈 |
| 前端提示 | SSE 端点用 `fetch POST + ReadableStream`(EventSource 不支持 POST),写进 README | 契约层面约束 |

## 目录结构

```
D:\study\llm\
├── docker-compose.yml          # db + backend,web 预留(注释)
├── .env.example                # LLM_PROVIDERS / API keys / DATABASE_URL(本地与 compose 两种主机名)
├── README.md
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic.ini + alembic/          # init -t async,首迁移建两张表
│   ├── app/
│   │   ├── main.py             # lifespan(engine/checkpointer setup)、路由、StaticFiles(/images)、CORS
│   │   ├── core/config.py      # pydantic-settings:DATABASE_URL、CHECKPOINT_DB_DSN、LLM_PROVIDERS、超时、图片目录
│   │   ├── core/registry.py    # ProviderConfig/ModelInfo/ModelRegistry(懒构建+缓存 ChatOpenAI)
│   │   ├── db/base.py          # DeclarativeBase、async_sessionmaker、get_session 依赖
│   │   ├── db/models.py        # Conversation / Message ORM
│   │   ├── db/checkpointer.py  # AsyncPostgresSaver 单例 + setup()(含已知坑规避)+ aclose()
│   │   ├── schemas/            # conversation.py / chat.py(ChatRequest、SSE 事件模型)
│   │   ├── api/                # deps.py、models.py、conversations.py、chat.py
│   │   ├── agent/manager.py    # AgentManager:model_id → CompiledStateGraph LRU 缓存
│   │   ├── agent/prompts.py    # system prompt(告知模型如何引用图片)
│   │   ├── agent/bridge.py     # astream_events → SSE 事件翻译器(★ 核心文件)
│   │   ├── services/chat_service.py  # 编排:校验→建 agent→落 user→流式→落 assistant(★ 核心文件)
│   │   └── tools/code_exec.py  # run_python_code 绘图工具
│   └── static/images/          # 运行时生成(compose 挂 volume)
└── web/                        # 预留
```

职责边界:api/ 只做 HTTP 层;chat_service.py 是唯一同时碰 DB 与 agent 的地方;bridge.py 纯翻译不碰 DB;registry/config 不依赖 FastAPI。

## API 契约

统一前缀 `/api`。前端注意:SSE 端点用 fetch POST + ReadableStream(EventSource 只支持 GET)。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针(compose healthcheck) |
| GET | `/api/models` | 模型列表,`available=false` = 该 provider 的 API key 未配置(前端置灰) |
| POST | `/api/conversations` | `{"title":"新对话"}` → 201 `{id,title,created_at}` |
| GET | `/api/conversations` | 列表,含 `message_count`、`updated_at` |
| GET | `/api/conversations/{id}/messages` | 升序消息列表 |
| POST | `/api/chat` | **SSE** 流式对话 |
| DELETE | `/api/conversations/{id}` | 204,级联删消息 + 清 checkpointer 线程 |
| GET | `/images/{filename}` | 生成的图片(StaticFiles) |

### POST /api/chat

```json
{"conversation_id":"3f2a...","model":"deepseek-chat","message":"画 y=sin(x)"}
```

SSE 帧:`event: <name>\ndata: <JSON>\n\n`。事件顺序:delta/reasoning 若干 → tool 若干 → `done` 或 `error` 结束。

- `delta`: `{"type":"delta","content":"增量片段"}` — token 级打字机
- `reasoning`(可选):deepseek-reasoner 等思考模型,`content` 思考增量
- `tool`: `{"type":"tool","name":"run_python_code","status":"start","args":...}` / `{"status":"end","result":{...}}`(args 截断 500 字)
  - `result` 为**结构化 JSON**(绘图工具专用):`{"status":"ok","image":"/images/<uuid>.png","stdout":"...","error":null}`,非绘图场景 `result` 退回字符串
  - **图片展示契约**:前端收到 `status:"end"` 且 `result.image` 存在 → 立即渲染 `<img src="/images/<uuid>.png">`(浏览器原生 GET,渐进加载,不等文本打完);`<img>` 不受 CORS 影响
- `done`: `{"type":"done","message_id","model","images":["/images/x.png"],"usage":{...}}`
- `error`: `{"type":"error","code":"model_not_found|provider_key_missing|conversation_not_found|upstream_error|timeout|internal","message":...}` — 发后流即结束;前置校验失败直接返回 4xx JSON

消息 `meta` JSONB 约定:`{"reasoning","tool_calls":[...],"images":[...],"usage":{...},"error"}`。

## 数据库

```sql
CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title VARCHAR(200) NOT NULL DEFAULT '新对话',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role VARCHAR(20) NOT NULL,          -- user | assistant
  content TEXT NOT NULL DEFAULT '',
  model VARCHAR(100),                 -- 生成该条消息的模型
  meta JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_conv_created ON messages(conversation_id, created_at);
```

- SQLAlchemy 2.0 async(asyncpg)管理业务表,Alembic 迁移(`alembic init -t async`);container 模式在容器内手动 `alembic upgrade head`
- checkpointer:`AsyncPostgresSaver.from_conn_string(CHECKPOINT_DB_DSN)`,lifespan 启动时 `setup()`;**已知坑**(langgraph issue #2570):全新库 setup 可能报 `current transaction is aborted`,先独立事务 `CREATE TABLE IF NOT EXISTS checkpoint_migrations(v INTEGER PRIMARY KEY)` 再 setup;关停 `aclose()`;`durability="async"` 保证中断后已完成 superstep 已落盘
- thread_id = str(conversation_id);删除会话时同步清 checkpointer 线程状态

## 模型注册中心

`.env`(pydantic-settings 解析 JSON 环境变量为 `list[ProviderConfig]`):

```
LLM_PROVIDERS=[
 {"name":"deepseek","base_url":"https://api.deepseek.com","api_key_env":"DEEPSEEK_API_KEY","models":["deepseek-chat","deepseek-reasoner"]},
 {"name":"openai","base_url":"https://api.openai.com/v1","api_key_env":"OPENAI_API_KEY","models":["gpt-4o-mini"]},
 {"name":"qwen","base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1","api_key_env":"DASHSCOPE_API_KEY","models":["qwen-plus","qwen-turbo"]}
]
```

`ModelRegistry.get_chat_model(model_id)` 懒构建并缓存:

```python
ChatOpenAI(model=model_id, base_url=..., api_key=..., temperature=0.7,
           timeout=60, max_retries=1, streaming=True)   # streaming 必须显式开,否则 on_chat_model_stream 不触发
```

国内平台全部走各自 OpenAI 兼容端点,无需适配器。model_id 全局唯一,冲突后者覆盖并告警。

## Streaming(bridge.py 核心)

```python
async def translate(graph, config, inputs):
    async for ev in graph.astream_events(inputs, config=config, version="v2"):
        # on_chat_model_stream → delta + reasoning(chunk.additional_kwargs 的 reasoning_content)
        # on_tool_start → tool start;on_tool_end → tool end;on_tool_error → error
```

两个要点:
1. 只翻译主 agent 的 token:按 `ev["metadata"]["langgraph_node"]` 过滤(预期 `"agent"`);**M3 第一步先打印全量事件名+节点名实测确认**,再定过滤常量
2. 不用 `graph.stream(stream_mode="updates")`——拿不到 token 增量,无法打字机

中断/错误:上游异常 → yield error 事件后正常收尾(不向 uvicorn 抛未捕获异常);客户端断开 → GeneratorExit,`finally` 中把 partial 文本落库(meta.error=true);不加全局超时(可选 asyncio.wait_for 包一层)。

## 绘图工具(code_exec.py)

`@tool run_python_code(code)` — 执行流程:
1. 预置头:`matplotlib.use("Agg")`、`plt.rcParams["font.sans-serif"]=["Noto Sans CJK SC"]`、`axes.unicode_minus=False`(中文不乱码)+ 用户代码
2. `asyncio.to_thread(subprocess.run, [sys.executable, "-u", "-c", wrapped], cwd=IMAGE_DIR, timeout=60, capture_output=True, env=scrubbed_env)` — env 剔除所有含 KEY 的变量
3. 超时杀进程树:Linux 容器用 `os.setsid` + `killpg`;Windows 本地用 psutil(children recursive)
4. 每次执行前清空 IMAGE_DIR;取最新 PNG 用 uuid4 重命名
5. **返回值(结构化 JSON)**:`{"status":"ok","image":"/images/<uuid>.png","stdout":"截断输出","error":null}`(失败时 status="error");agent 最终回答自然引用该 URL。图片不走 SSE 二进制,SSE 只传 URL 信号,前端 `<img>` 独立 GET 加载
6. 安全说明(诚实标注):子进程与后端同权限,LLM 代码属未受信输入,个人项目可接受;backlog 可选 docker 沙箱执行

## Docker

- **backend/Dockerfile**:`python:3.13-slim` + `fonts-noto-cjk`(否则图表中文乱码);先 COPY pyproject 装依赖再 COPY 代码(层缓存);`CMD uvicorn app.main:app`
- **docker-compose.yml**:`db`(postgres:17-alpine + healthcheck + pgdata volume)、`backend`(depends_on: db service_healthy、env_file: .env、images_data volume、healthcheck)、`web` 预留注释
- 本地开发模式:只 `docker compose up -d db`,uvicorn 在宿主跑(.env 里 DATABASE_URL 用 localhost);全容器模式用 db 主机名 —— .env.example 两种都给出

## 依赖(backend/pyproject.toml)

```
fastapi>=0.115  uvicorn[standard]>=0.30  sqlalchemy[asyncio]>=2.0.51  asyncpg>=0.30
alembic>=1.14  pydantic-settings>=2.4  langchain-core>=1.4  langchain-openai>=1.4
langgraph>=1.2  deepagents==0.6.12  langgraph-checkpoint-postgres>=3.1.1
psycopg[binary]>=3.3  matplotlib>=3.9  psutil>=6
```

## 实施顺序(里程碑)

- **M0 骨架**:pyproject、main.py(/health)、config、SQLAlchemy engine、Alembic 首迁移、compose 拉起 postgres。验收:compose 起 db;`alembic upgrade head` 成功;`/health` 200
- **M1 模型注册 + 流式**:registry、GET /api/models、POST /api/chat 先直接消费 `ChatOpenAI.astream()`(验证 SSE 管道,再上 agent)。验收:模型列表含 available 标记;curl -N 见逐 token delta + done;错误模型返回 error 事件;第二个 provider 可切换
- **M2 历史**:CRUD、chat 落库、列表/详情/删除级联、DB 回放接续(普通 ChatOpenAI + 回放,为 M3 留接口)。验收:重启后同会话能接上文;DELETE 204 且级联清空
- **M3 agent + 工具 + 图片**:AgentManager LRU、checkpointer.py(含坑规避)、bridge.py(先打印事件确认节点名)、run_python_code、StaticFiles、system prompt;实测默认工具列表,能禁则禁。验收:"画 y=sin(x)" 走完 tool start→end→delta→done;图片可下载且是合法 PNG;同会话追问"改成 cos"能基于 checkpointer 上下文;换模型上下文连续;deepseek-reasoner 可选验证 reasoning 事件
- **M4 容器化**:Dockerfile、compose 全链路、.env.example、README(启动说明 + curl 手册)。验收:`docker compose up --build -d` 全链路通;`down` 后 `up` 数据仍在(volume)

## 验证(curl 级,Git Bash)

```bash
docker compose up -d db && alembic upgrade head && uvicorn app.main:app --reload
curl -s localhost:8000/api/models | python -m json.tool
CID=$(curl -s -X POST localhost:8000/api/conversations -H 'Content-Type: application/json' \
  -d '{"title":"测试"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
# 流式 + 上下文 + 切模型(期望回答"暗号是西瓜"):
curl -N -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d "{\"conversation_id\":\"$CID\",\"model\":\"deepseek-chat\",\"message\":\"你好,记住暗号:西瓜\"}"
curl -N -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d "{\"conversation_id\":\"$CID\",\"model\":\"qwen-plus\",\"message\":\"暗号是什么?\"}"
# 图片端到端:done.images 非空 → curl -o chart.png localhost:8000/images/x.png → file 显示 PNG
# 错误路径:不存在的 model → event: error / code: model_not_found
# 中断恢复:流式进行中 Ctrl+C,重发同会话消息可继续
```

## 风险与注意点

1. 包名 `deepagents`(非 langchain-deepagents),锁 0.6.12(0.7.x breaking changes)
2. checkpoint-postgres 3.x 必须 `setup()`;新库 setup 事务坑先建 checkpoint_migrations 表;连接用完 aclose
3. deepagents 默认随附 todo/file/execute 工具——M3 实测能否移除默认 execute(最大风险源),能禁则禁
4. bridge 节点名过滤需一次实测校准(打印全量事件)
5. 前端 SSE 须用 fetch POST + ReadableStream(写进 README)
6. 开发期 CORS allow_origins=["*"],README 注明上生产前收紧
