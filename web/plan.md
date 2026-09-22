# Vue3 前端实现计划(DeepSeek 风格对话平台)

## Context

后端(FastAPI + deepagents + PostgreSQL)已实现并 21/21 项验收通过,API 契约稳定(见 `backend/plan.md`、`backend/e2e_test.py`)。现在实现前端:Vue3 + TypeScript + Vite + Naive UI + Pinia + vue-router,页面布局参照 DeepSeek 网页版,亮/暗双主题,Docker + nginx 部署(启用 compose 预留的 web 服务)。web/ 目录当前为空。

用户已确认:Naive UI、亮/暗双主题、Docker 部署。

## 后端契约要点(前端硬约束,Plan agent 已核查代码确认)

- **SSE 必须 fetch POST + ReadableStream**(EventSource 只支持 GET);帧格式 `event: <name>\ndata: <json>\n\n`,data.type 恒等于 event 名
- 事件:`delta`(token 增量)/ `reasoning`(思考增量)/ `tool`(start/end,end 时 result 为**截断字符串**,内含 JSON `{"status","image","stdout","error"}`,需 JSON.parse 失败回退文本)/ `done`(message_id/images/usage)/ `error`(code/message,发后无 done)
- 4xx 前置校验非 SSE:400 detail 是 `{"code","message"}` 对象;404 是字符串 → client 层兼容两种形态
- 图片相对路径 `/images/x.png` 挂**根路径**(非 /api 下),nginx 反代和 vite proxy 需单独处理 /images
- assistant 消息 meta:`{images, tool_calls, usage, error?}`;**reasoning 不落库**(回放无思考过程,组件自动隐藏)
- 后端**无改标题接口** → 标题自动生成走"懒创建"(首条消息发出时才 POST 建会话,title=首消息前 20 字)

## 技术选型

| 决策 | 方案 |
|---|---|
| 脚手架 | `npm create vite@latest web -- --template vue-ts`(Vue 3.5 + Vite 7,Node ≥ 20.19;Docker 用 node:22-alpine) |
| UI | naive-ui ^2.4(`n-config-provider` + `darkTheme`、popconfirm、n-select 分组、textarea autosize) |
| 状态/路由 | pinia ^3、vue-router ^4.5 |
| Markdown | markdown-it ^14 + highlight.js ^11;代码块高亮主题自写 20 行 CSS 变量版(双主题适配);mermaid 不做,markdown.ts 留 fence 钩子 |
| SSE | 自写解析器(~60 行,AbortController 原生控制),不用 sse.js |
| 样式 | 原生 CSS + CSS 变量(variables.css 亮/暗两套),不用 Tailwind/sass |
| HTTP | 原生 fetch(不装 axios) |

依赖:`vue vue-router pinia naive-ui markdown-it highlight.js`;dev:脚手架默认 + `@types/markdown-it`。

## 目录结构

```
web/
├── index.html / package.json / tsconfig*.json / env.d.ts
├── vite.config.ts              # dev 代理 /api、/images → localhost:8000
├── Dockerfile                  # multi-stage:node 构建 → nginx 托管
├── nginx.conf                  # 反代 /api、/images + SPA fallback + SSE 关 buffering
└── src/
    ├── main.ts / App.vue       # n-config-provider(条件 darkTheme,locale=zhCN)+ message/dialog provider
    ├── styles/                 # variables.css(双主题变量)/ markdown.css / base.css
    ├── router/index.ts         # /(新对话)与 /chat/:id(历史)共用 ChatPage
    ├── types/index.ts          # ModelInfo/Conversation/ChatMessage/ToolCall/SSE 事件类型
    ├── api/
    │   ├── client.ts           # fetchJson<T>,统一非 2xx → ApiError{status,code,message}(兼容两种 detail)
    │   ├── conversations.ts    # create/list/getMessages/remove
    │   ├── models.ts           # listModels
    │   └── stream.ts           # ★ streamChat(body, handlers):fetch POST + ReadableStream 分帧解析,返回 AbortController
    ├── stores/
    │   ├── theme.ts            # mode('light'|'dark'|'system')+ matchMedia 监听 + html[data-theme]
    │   ├── model.ts            # 模型列表 + currentModel(localStorage 'llm-last-model')
    │   └── conversation.ts     # ★ 会话列表/当前会话/messages/pending(流式中唯一可变对象)/send/stop/resync
    ├── utils/
    │   ├── markdown.ts         # markdown-it 单例(breaks/linkify/html:false + hljs 高亮 + image 渲染器补全相对路径)
    │   └── asset.ts            # resolveAssetUrl('/images/x.png') → 同源原样 / VITE_API_BASE 前缀
    ├── composables/useAutoScroll.ts   # rAF 节流 + 距底部 <80px 才跟随 + 回到底部按钮
    ├── components/
    │   ├── layout/SideBar.vue + ChatHeader.vue
    │   ├── conversation/ConversationItem.vue   # 标题/消息数/高亮/hover 删除(popconfirm)
    │   ├── chat/MessageList.vue + UserMessage.vue + AssistantMessage.vue
    │   │     + ReasoningBlock.vue + MarkdownContent.vue + ToolCard.vue
    │   │     + ErrorBanner.vue + ChatInput.vue + ScrollToBottom.vue
    │   └── common/ModelSelect.vue(按 provider 分组,available=false 置灰)+ ThemeToggle.vue(三态)
    └── pages/ChatPage.vue      # 唯一页面:侧栏 260px + 主区(顶栏/消息流/输入框)
```

## 组件树

```
App.vue → n-config-provider(darkTheme 条件) → router-view
└── ChatPage(flex: 侧栏 260px + 主区)
    ├── SideBar:新对话按钮 | 会话列表 | 底部 ThemeToggle
    └── 主区:ChatHeader(标题|ModelSelect|ThemeToggle)
         ├── MessageList:UserMessage×n + AssistantMessage×n
         │   └── AssistantMessage:ReasoningBlock → MarkdownContent → ToolCard×n → ErrorBanner → UsageFooter
         └── ChatInput(流中显示停止按钮)
```

## 核心实现细节

### 1. SSE 解析器(api/stream.ts)
- `fetch('/api/chat', {method:'POST', body, signal})`;**非 2xx** 先 res.json() 读 detail → 抛 ApiError(不进流解析)
- `res.body.getReader()` + `TextDecoder('utf-8',{stream:true})`,buf 累积按 `\n\n` 切帧;每帧正则取 `event:` 与 `data:`(多行 data 用 \n 连接防御);JSON.parse 失败静默丢弃
- 按 event 名分发到 handlers(delta/reasoning/tool/done/error);返回 AbortController 供停止

### 2. 流式状态编排(stores/conversation.ts)
- state:`list / currentId / messages(固化) / pending(流式中唯一 mutable) / streaming / loading`
- `send(text)`:
  1. `currentId === null` → **懒创建** POST conversations `{title: text.trim().slice(0,20) || "新对话"}` → router.push
  2. user 消息乐观 push(后端已即时落库)
  3. `pending={status:'streaming', content, reasoning, toolCalls:[], images:[], usage}` + streamChat
  4. handlers 增量累积:`delta→content+=`、`reasoning→reasoning+=`、`tool start→toolCalls.push`、`tool end→更新 status/result`,**result 可 parse 且含 image → 立即 pending.images.push(图片卡即时渲染)**
  5. `done` → pending 固化进 messages(带 message_id/usage)、pending=null、刷新会话列表(消息数/排序)
  6. `error` → pending.status='error' 固化(后端已把部分内容落库);网络异常 → 'interrupted' 保留内容可重试
- `stop()` = abort + status='interrupted';**中断恢复** = 进会话/刷新时 fetchMessages() 从服务端重建(后端已落库的 partial 自动恢复)

### 3. Markdown(utils/markdown.ts)
- 单例 markdown-it `{html:false, linkify:true, breaks:true}`(html:false 防 XSS)
- hljs.highlight 按语言 → 回退 highlightAuto → 回退转义;包 `<pre class="hljs">`
- **覆写 image 渲染器**:`token.attrSet('src', resolveAssetUrl(src))`
- 样式全在 markdown.css(CSS 变量配色,图片 max-width:100%)

### 4. 消息项渲染
- UserMessage:右对齐气泡纯文本
- AssistantMessage 顺序(流式与回放统一):ReasoningBlock(仅流中有数据;流中"思考中…"展开,结束"已深度思考"收起,回放无则隐藏)→ MarkdownContent → ToolCard×n(流中实时追加;回放时 meta.tool_calls 渲染静态卡)→ ErrorBanner(+重试重发)→ UsageFooter(token 数小字)
- ToolCard:名称(中文映射)+ 状态(运行中 spinner/已完成)+ args 摘要;result JSON.parse 成功且 image → 立即 `<img src=resolveAssetUrl(image)>`;失败回退文本,正则兜底提取 image 路径

### 5. 模型切换与主题
- ModelSelect:n-select 按 provider 分组,`disabled: !available` 置灰(未配 key);切换只影响后续请求(每发 send 取 currentModel 快照);同会话换模型上下文连续由后端保证
- ThemeToggle 三态(亮/暗/跟随系统),localStorage 'llm-theme';resolved 写 `document.documentElement.dataset.theme`,Naive UI 侧 `:theme="resolved==='dark' ? darkTheme : null`;色板全走 variables.css 变量

### 6. 错误与边界
| 场景 | 处理 |
|---|---|
| 4xx 前置校验 | n-message 提示 + 移除乐观 user 消息(后端未落库)+ 恢复输入 |
| 404 | "会话不存在" → push('/') |
| SSE error 事件 | pending 固化 + ErrorBanner + 重试 |
| 网络断开 | ErrorBanner"连接中断" + 重试 |
| 流中断(EOF 无 done) | pending 保留 + fetchMessages() 恢复 |
| 输入边界 | 空消息禁用;超 20000 字符拦截;`isComposing` 防中文输入法 Enter 误发 |

### 7. ChatInput
n-input textarea autosize(1–8 行);Enter 发送 / Shift+Enter 换行(keydown 检查 shiftKey + isComposing);流中发送禁用 + 停止按钮,输入框保留可编辑。

## Docker 部署

**web/Dockerfile**:`node:22-alpine` 构建(`npm ci` + `npm run build`)→ `nginx:1.27-alpine` 拷贝 dist + nginx.conf

**web/nginx.conf**:
```nginx
location /api/ { proxy_pass http://backend:8000; proxy_http_version 1.1;
  proxy_set_header Host $host; proxy_buffering off; proxy_cache off; proxy_read_timeout 300s; }
location /images/ { proxy_pass http://backend:8000; proxy_cache_valid 200 10m; }
location / { try_files $uri $uri/ /index.html; }   # SPA fallback
location /assets/ { expires 7d; }
```
(后端已发 `X-Accel-Buffering: no`,nginx 侧关 buffering 双保险;proxy_pass 不带 URI 段避免剥路径)

**docker-compose.yml**:启用 web 服务 `build: ./web, ports: ["3000:80"], depends_on: [backend]`

## 开发环境

vite.config.ts:server.proxy `'/api': 'http://localhost:8000'`、`'/images': 'http://localhost:8000'`(同源后无 CORS、图片相对路径天然可用、SSE 流式透传)。开发:前端 `npm run dev`(5173)+ 后端 `python run.py` + db 容器。可选 `VITE_API_BASE` 直连后端。

## 实施顺序(里程碑 + 验收)

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **W0 脚手架+主题+骨架** | vite 脚手架、依赖、三 store 骨架、App.vue 主题接入、variables.css 双主题、SideBar/ChatHeader/ChatInput 静态布局、路由 | 亮/暗切换全站生效且 localStorage 记忆;刷新保持;/ 页可输入不报错 |
| **W1 会话与消息(非流式)** | api/ 三层、conversation store 非流式部分、侧栏列表/删除确认/切换、fetchMessages 回放(markdown+代码高亮+表格+图片)、ModelSelect | curl 造的数据完整回放;删除弹确认级联生效;模型下拉分组、未配 key 置灰 |
| **W2 SSE 流式** | stream.ts 解析器、send/stop、打字机、done/error 固化、乐观 user 消息、4xx/网络错误、自动滚动、isComposing | 逐 token 输出;停止截断保留;流中禁用发送/停止可用;后端停掉不白屏;刷新历史完整 |
| **W3 高级渲染** | ReasoningBlock 折叠、ToolCard+图片即时渲染、ErrorBanner+重试、UsageFooter、懒创建标题、中断恢复、ScrollToBottom | "画 y=sin(x)" → 工具卡"运行中"→ 图片卡先于正文出现;reasoner → 思考块流中展开/结束收起/回放无;首条消息即真实标题 |
| **W4 Docker 部署** | web/Dockerfile、nginx.conf、compose 启用 | `docker compose up --build` 后 3000 全流程可用(流式/图片/历史/暗色);深层路径刷新不 404;SSE 无缓冲延迟 |

## 验证

- curl 级:造会话/发流式/删会话(复用 backend/e2e_test.py 思路)
- 浏览器手工验收 11 项(首次打开/打字机/停止/切换删除会话不崩/回放完整/懒创建标题/模型切换思考块/画图即时图片/暗色一致/错误路径/Docker 3000 端口全流程)
- 重点回归:SSE 无缓冲延迟、图片经反代加载、深层路径刷新
