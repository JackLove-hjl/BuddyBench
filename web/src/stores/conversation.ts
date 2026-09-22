import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  compactConversation,
  createConversation,
  getMessages,
  listConversations,
  removeConversation,
  updateConversation,
} from '../api/conversations'
import { setWorkspace } from '../api/workspaces'
import { approveChat, streamChat } from '../api/stream'
import { useModelStore } from './model'
import { router } from '../router'
import type {
  ApprovalAction,
  ChatMessage,
  Conversation,
  PendingMessage,
  PendingSegment,
  Permission,
  SSEApprovalEvent,
  SSECompactEvent,
  ToolEvent,
  SSEDoneEvent,
  SSEErrorEvent,
  SSEToolArgsEvent,
  SSEToolEvent,
  ToolCall,
} from '../types'

export interface SendResult {
  ok: boolean
  error?: { code: string; message: string }
}

/** 发送附件(与 ChatInput 组件类型对应,避免循环依赖)。 */
export interface SendAttachment {
  url: string
  filename: string
  kind: 'image' | 'file'
  content?: string
}

function optimisticUserMessage(content: string, attachments?: SendAttachment[]): ChatMessage {
  const meta: ChatMessage['meta'] = {}
  if (attachments?.length) {
    meta.attachments = attachments.map((a) => ({ url: a.url, filename: a.filename, kind: a.kind }))
  }
  return { id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, role: 'user', content, meta, created_at: new Date().toISOString() }
}

function toAssistantMessage(
  id: string,
  pending: PendingMessage,
  model?: string,
  extraMeta: Partial<ChatMessage['meta']> = {},
): ChatMessage {
  return {
    id,
    role: 'assistant',
    content: pending.content,
    model: model || pending.model,
    meta: {
      images: pending.images.length ? pending.images : undefined,
      tool_calls: pending.toolCalls.length ? pending.toolCalls : undefined,
      tool_records: pending.toolCalls.length ? pending.toolCalls : undefined,
      // 深度思考全文与时间线,保证刷新后不丢失、按执行顺序回放
      reasoning: pending.reasoning || undefined,
      segments: pending.segments.length ? pending.segments : undefined,
      // 原始事件流:比 segments 多出正文 delta,可精确还原交错顺序
      events: pending.events?.length ? pending.events : undefined,
      // 待审批动作:挂起态固化后刷新页面仍可继续批准/拒绝
      pending_approval: pending.approval?.length ? { actions: pending.approval } : undefined,
      usage: pending.usage && (pending.usage.prompt_tokens || pending.usage.completion_tokens)
        ? pending.usage
        : undefined,
      error: pending.error,
      ...extraMeta,
    },
    created_at: new Date().toISOString(),
  }
}

const IMAGE_RE = /\/images\/[0-9a-fA-F-]{36}\.png/

function extractImage(result: string): string | null {
  const m = IMAGE_RE.exec(result)
  return m ? m[0] : null
}

/**
 * 组装 HITL 决策。中间件要求「决策数量 = 挂起动作数量」,而一次挂起里可能有多个动作
 * (比如模型同一轮同时调了 ask_user 与 exit_plan_mode),数量不一致会直接报错。
 *
 * - 用户在问题卡上作答(respond):其余动作一律驳回 —— 此刻他并没有批准任何危险操作或计划,
 *   驳回文案会作为工具结果提示模型"先按答案修订,再重新提交";
 * - 其他情况(审批卡 / 计划评审):按用户点的那一个决策统一处理。
 */
function buildDecisions(
  actions: ApprovalAction[],
  decide: 'approve' | 'reject' | 'respond',
  message?: string,
): { type: 'approve' | 'reject' | 'respond'; message?: string }[] {
  if (!actions.length) return [{ type: decide, message }]
  return actions.map((a) => {
    if (decide === 'respond' && a.name !== 'ask_user') {
      return { type: 'reject', message: '用户正在回答提问,请先据此修订方案,再重新提交。' }
    }
    if (decide !== 'respond' && a.name === 'ask_user') {
      return { type: 'reject', message: '用户跳过了提问,请按你的判断决定并在计划里写明假设。' }
    }
    return { type: decide, message }
  })
}

export const useConversationStore = defineStore('conversation', () => {
  const list = ref<Awaited<ReturnType<typeof listConversations>>['items']>([])
  const currentId = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const pending = ref<PendingMessage | null>(null)
  const streaming = ref(false)
  const loading = ref(false)
  // 当前会话的权限/工作区/计划模式(从会话列表/详情同步)。
  // 同时持久化到 localStorage:开启新对话时默认继承上一次对话的工作区/权限/计划模式
  const permission = ref<Permission>(
    (localStorage.getItem('llm-permission') as Permission) || 'read_only',
  )
  const workspacePath = ref<string | null>(localStorage.getItem('llm-workspace') || null)
  const planMode = ref(localStorage.getItem('llm-plan') === '1')
  // 上下文使用情况(最近一次响应),供对话框发送按钮左侧圆圈展示
  const contextUsage = ref<{ used: number; window: number }>({ used: 0, window: 128000 })

  let controller: AbortController | null = null
  const modelStore = useModelStore()

  /**
   * 记录一条原始事件。
   *
   * 注意一个容易踩的坑:SSE 是**逐 chunk** 下发的(`bridge.translate` 每个 token 就 yield 一帧),
   * 而后端落库的 `meta.events` 是**合并后**的(`_EventRecorder` 把同类连续增量并成一条)。
   * 如果这里按"一帧一条"追加,流式期间一次连续思考会被渲染成几十个「已深度思考」块,
   * 刷新后又变回 1 个 —— 同一个回答两种样子。因此这里做与后端一致的合并:
   * 同类增量(正文/思考)只要紧邻就并入上一条,并保留首条的时间戳。
   */
  function pushEvent(ev: ToolEvent) {
    const p = pending.value
    if (!p) return
    if (!p.events) p.events = []
    const last = p.events[p.events.length - 1]
    if (ev.kind === 'delta') {
      if (last?.kind === 'delta') {
        last.text += ev.text
        return
      }
    } else if (ev.kind === 'reasoning') {
      if (last?.kind === 'reasoning') {
        last.text += ev.text
        return
      }
    }
    p.events.push(ev)
  }

  /** 追加正文增量 */
  function handleDelta(content: string) {
    const p = pending.value
    if (!p) return
    p.content += content
    pushEvent({ at: Date.now(), kind: 'delta', text: content })
  }

  /** 追加推理增量:更新全文,同时按连续块维护时间线 */
  function handleReasoning(content: string) {
    const p = pending.value
    if (!p) return
    p.reasoning += content
    pushEvent({ at: Date.now(), kind: 'reasoning', text: content })
    const last = p.segments[p.segments.length - 1]
    if (last && last.kind === 'reasoning') {
      last.content += content
    } else {
      p.segments.push({ kind: 'reasoning', content })
    }
  }

  /** 上下文压缩(自动预判 / 溢出恢复) */
  function handleCompact(ev: SSECompactEvent) {
    const p = pending.value
    if (!p || !ev.ok || !ev.summary) return
    p.segments.push({ kind: 'compact', summary: ev.summary })
  }

  /** 参数流卡片收尾:本轮已停(中断/结束/停止)时不能留着"正在生成",否则卡片会一直转圈 */
  function settleArgsCards(p: PendingMessage, status: 'paused' | 'end') {
    for (const call of p.toolCalls) {
      if (call.status === 'args') call.status = status
    }
  }

  /** 危险操作挂起:固化当前产出并记录待审批动作 */
  function handleApproval(ev: SSEApprovalEvent) {
    const p = pending.value
    if (!p) return
    // 工具已写完参数、正等着人工批准:卡片停在"待批准"(内容仍可查看),不再转圈
    settleArgsCards(p, 'paused')
    p.approval = ev.actions
    messages.value.push(toAssistantMessage(`local-approval-${Date.now()}`, p))
    pending.value = null
    streaming.value = false
    controller = null
    void refreshList()
  }

  /**
   * 工具参数流:模型正在生成参数(也就是"正在写的那段代码")。
   *
   * 首个增量到来时就把卡片画出来,否则从"决定写文件"到"工具开始执行"之间完全没有反馈,
   * 大文件看起来像卡死。这些增量**不进事件流**(events),内容由 on_tool_start 的 payload 完整落库。
   */
  function handleToolArgs(ev: SSEToolArgsEvent) {
    const p = pending.value
    if (!p) return
    let call = [...p.toolCalls].reverse().find((t) => t.name === ev.name && t.status === 'args')
    if (!call) {
      call = { name: ev.name, status: 'args', stream: {} }
      p.toolCalls.push(call)
      p.segments.push({ kind: 'tool', call })
    }
    if (ev.field && ev.delta) {
      call.stream = { ...(call.stream || {}), [ev.field]: (call.stream?.[ev.field] || '') + ev.delta }
    }
  }

  function handleTool(ev: SSEToolEvent) {
    const p = pending.value
    if (!p) return
    if (ev.status === 'start') {
      // 参数流阶段已经画过卡:原地升级成"执行中",避免同一个工具出现两张卡
      const streaming = [...p.toolCalls]
        .reverse()
        .find((t) => t.name === ev.name && t.status === 'args')
      if (streaming) {
        streaming.status = 'start'
        streaming.args = ev.args
        streaming.started_at = ev.started_at
        if (ev.payload) streaming.payload = ev.payload
        pushEvent({ at: Date.now(), kind: 'tool_start', name: ev.name, args: ev.args })
        return
      }
      const call: ToolCall = {
        name: ev.name,
        status: 'start',
        args: ev.args,
        payload: ev.payload,
        started_at: ev.started_at,
      }
      p.toolCalls.push(call)
      p.segments.push({ kind: 'tool', call })
      // 事件流里补上工具边界:否则流式期间的 blocks 只有思考/正文,工具卡要刷新后才出现
      pushEvent({ at: Date.now(), kind: 'tool_start', name: ev.name, args: ev.args })
      return
    }
    // end:找到同名进行中的工具卡更新;找不到则追加
    const last = p.toolCalls.find((t) => t.name === ev.name && t.status === 'start')
    const endCall: ToolCall = {
      name: ev.name,
      status: 'end',
      result: ev.result,
      started_at: last?.started_at,
      duration_ms: ev.duration_ms,
      exit_code: ev.exit_code,
      ok: ev.ok,
    }
    if (last) {
      Object.assign(last, endCall)
    } else {
      p.toolCalls.push(endCall)
      p.segments.push({ kind: 'tool', call: endCall })
    }
    // 工具结束也是事件流里的一个边界(与后端 on_tool_end 的 recorder.push 对应)
    pushEvent({
      at: Date.now(),
      kind: 'tool_end',
      name: ev.name,
      result: ev.result,
      duration_ms: ev.duration_ms,
      exit_code: ev.exit_code,
      ok: ev.ok,
    })
    // result 可 parse 且含 image → 图片卡即时渲染
    if (ev.result) {
      try {
        const parsed = JSON.parse(ev.result) as { image?: string }
        if (parsed.image && !p.images.includes(parsed.image)) p.images.push(parsed.image)
      } catch {
        // 非 JSON,回退正则提取
      }
      const img = extractImage(ev.result)
      if (img && !p.images.includes(img)) p.images.push(img)
    }
  }

  function finalize(ev: SSEDoneEvent) {
    const p = pending.value
    if (!p) return
    settleArgsCards(p, 'end')
    messages.value.push(toAssistantMessage(ev.message_id, p, ev.model, {
      images: ev.images.length ? ev.images : undefined,
      usage: ev.usage && (ev.usage.prompt_tokens || ev.usage.completion_tokens) ? ev.usage : undefined,
    }))
    // 记录上下文使用情况(本次请求 prompt_tokens ≈ 已占用上下文)
    if (ev.usage?.prompt_tokens) {
      contextUsage.value.used = ev.usage.prompt_tokens
      contextUsage.value.window = ev.context_window || contextUsage.value.window
    }
    pending.value = null
    streaming.value = false
    controller = null
    void refreshList()
  }

  function fail(ev: SSEErrorEvent, httpStatus?: number): SendResult {
    const p = pending.value
    if (httpStatus) {
      // 前置校验失败(后端未落库):移除乐观 user 消息,清 pending,恢复输入
      messages.value.pop()
      pending.value = null
      streaming.value = false
      controller = null
      return { ok: false, error: { code: ev.code, message: ev.message } }
    }
    if (!p) return { ok: false, error: { code: ev.code, message: ev.message } }
    settleArgsCards(p, 'end')
    p.status = ev.code === 'network' ? 'interrupted' : 'error'
    p.error = ev.message
    messages.value.push(toAssistantMessage(`local-err-${Date.now()}`, p))
    pending.value = null
    streaming.value = false
    controller = null
    void refreshList()
    return { ok: false, error: { code: ev.code, message: ev.message } }
  }

  /** 发送消息:懒创建会话(携带权限/工作区) → 乐观 user → 流式累积 → done/error 固化 */
  async function send(text: string, attachments?: SendAttachment[], skipUserPush = false): Promise<SendResult> {
    if (streaming.value) return { ok: false, error: { code: 'busy', message: '正在回复中' } }
    const trimmed = text.trim()
    if (!trimmed && !(attachments?.length)) {
      return { ok: false, error: { code: 'empty', message: '消息不能为空' } }
    }

    // 标题:优先取正文前 20 字符;正文为空(纯附件)用文件名
    let title = trimmed.slice(0, 20) || '新对话'
    if (!trimmed && attachments?.length) {
      title = attachments[0].filename.slice(0, 20)
    }

    let convId = currentId.value
    if (!convId) {
      try {
        const conv = await createConversation({
          title,
          permission: permission.value,
          workspace_path: workspacePath.value,
          plan_mode: planMode.value,
        })
        convId = conv.id
        currentId.value = convId
        syncFromConversation(conv)
        if (router.currentRoute.value.path !== `/chat/${convId}`) {
          void router.push(`/chat/${convId}`)
        }
        await refreshList()
      } catch (e) {
        return { ok: false, error: { code: 'create_failed', message: (e as Error).message } }
      }
    }

    const model = modelStore.currentModel
    if (!model) {
      return { ok: false, error: { code: 'no_model', message: '请先选择可用模型' } }
    }

    if (!skipUserPush) messages.value.push(optimisticUserMessage(trimmed || '[附件]', attachments))
    pending.value = {
      status: 'streaming',
      content: '',
      reasoning: '',
      toolCalls: [] as ToolCall[],
      segments: [] as PendingSegment[],
      events: [] as ToolEvent[],
      images: [],
      usage: {},
      model,
    }
    streaming.value = true

    const payload = { conversation_id: convId!, model, message: trimmed }
    // 仅在有附件时附加 attachments
    const chatBody =
      attachments?.length
        ? { ...payload, attachments: attachments.map((a) => ({ url: a.url, filename: a.filename, kind: a.kind, content: a.content || '' })) }
        : payload

    return await new Promise<SendResult>((resolve) => {
      const ctrl = streamChat(
        chatBody as { conversation_id: string; model: string; message: string; attachments?: unknown[] },
        {
          onDelta: handleDelta,
          onReasoning: handleReasoning,
          onTool: handleTool,
          onToolArgs: handleToolArgs,
          onCompact: handleCompact,
          onApproval: (ev) => {
            handleApproval(ev)
            resolve({ ok: true })
          },
          onDone: (ev) => {
            finalize(ev)
            resolve({ ok: true })
          },
          onError: (ev, meta) => resolve(fail(ev, meta?.httpStatus)),
        },
      )
      controller = ctrl
    })
  }

  /** 停止流式:abort + 已生成内容固化为 interrupted 消息(可重试) */
  function stop() {
    controller?.abort()
    controller = null
    const p = pending.value
    if (p && p.status === 'streaming') {
      settleArgsCards(p, 'end')
      p.status = 'interrupted'
      if (p.content.trim() || p.images.length || p.toolCalls.length) {
        messages.value.push(toAssistantMessage(`local-stop-${Date.now()}`, p))
      }
      pending.value = null
      streaming.value = false
      void refreshList()
    }
  }

  /** 提交人工决策并继续接收恢复流。
   *
   * - approve / reject:批准或驳回(计划评审「继续修改」用 reject + message 回传反馈);
   * - respond:ask_user 的作答,message 就是用户答案正文,作为工具结果回给模型。
   */
  async function approve(
    decide: 'approve' | 'reject' | 'respond',
    message?: string,
  ): Promise<SendResult> {
    if (streaming.value) return { ok: false, error: { code: 'busy', message: '正在回复中' } }
    const convId = currentId.value
    if (!convId) return { ok: false, error: { code: 'no_conversation', message: '当前没有会话' } }
    const model = modelStore.currentModel
    if (!model) return { ok: false, error: { code: 'no_model', message: '请先选择可用模型' } }

    // 清掉待审批标记(最近一条挂起中的助手消息),并判断这是不是计划评审
    let planReview = false
    let actions: ApprovalAction[] = []
    for (let i = messages.value.length - 1; i >= 0; i--) {
      const pa = messages.value[i].meta.pending_approval
      if (pa) {
        actions = pa.actions
        planReview = pa.actions.some((a) => a.name === 'exit_plan_mode')
        messages.value[i].meta.pending_approval = undefined
        break
      }
    }
    // 批准计划时后端会关闭计划模式(与 api/chat.py 同一判定规则),前端同步跟随
    if (planReview && decide === 'approve') {
      planMode.value = false
      persistDefaults()
    }

    pending.value = {
      status: 'streaming',
      content: '',
      reasoning: '',
      toolCalls: [] as ToolCall[],
      segments: [] as PendingSegment[],
      events: [] as ToolEvent[],
      images: [],
      usage: {},
      model,
    }
    streaming.value = true

    return await new Promise<SendResult>((resolve) => {
      const ctrl = approveChat(
        { conversation_id: convId, model, decisions: buildDecisions(actions, decide, message) },
        {
          onDelta: handleDelta,
          onReasoning: handleReasoning,
          onTool: handleTool,
          onToolArgs: handleToolArgs,
          onCompact: handleCompact,
          onApproval: (ev) => {
            handleApproval(ev)
            resolve({ ok: true })
          },
          onDone: (ev) => {
            finalize(ev)
            resolve({ ok: true })
          },
          onError: (ev, meta) => resolve(fail(ev, meta?.httpStatus)),
        },
      )
      controller = ctrl
    })
  }

  /** 重试上一条失败/中断的 user 消息 */
  async function retry(): Promise<SendResult> {
    for (let i = messages.value.length - 1; i >= 0; i--) {
      if (messages.value[i].role === 'user') {
        const text = messages.value[i].content
        messages.value.splice(i)
        return send(text, undefined, true)
      }
    }
    return { ok: false, error: { code: 'no_user', message: '没有可重试的消息' } }
  }

  /** 从会话对象同步 权限/工作区/计划模式(列表、切换会话、新建后共用同一份逻辑) */
  function syncFromConversation(conv: Conversation) {
    permission.value = conv.permission
    workspacePath.value = conv.workspace_path
    planMode.value = conv.plan_mode
    persistDefaults()
  }

  async function refreshList() {
    try {
      const resp = await listConversations()
      list.value = resp.items
      // 同步当前会话的权限/工作区/计划模式(列表优先;列表无该项时回退当前值)
      if (currentId.value) {
        const cur = resp.items.find((c) => c.id === currentId.value)
        if (cur) syncFromConversation(cur)
      }
    } catch {
      // 列表加载失败静默
    }
  }

  /** 切换会话:停止当前流并拉取该会话完整消息 */
  async function selectConversation(id: string) {
    if (streaming.value) stop()
    currentId.value = id
    messages.value = []
    pending.value = null
    loading.value = true
    try {
      const resp = await getMessages(id)
      messages.value = resp.items
      // 从列表同步权限/工作区/计划模式(selectConversation 前先 refreshList 保证列表含该项)
      const cur = list.value.find((c) => c.id === id)
      if (cur) {
        syncFromConversation(cur)
      } else {
        await refreshList()
        const cur2 = list.value.find((c) => c.id === id)
        if (cur2) syncFromConversation(cur2)
      }
    } catch (e) {
      throw e
    } finally {
      loading.value = false
    }
  }

  function newConversation() {
    if (streaming.value) stop()
    currentId.value = null
    messages.value = []
    pending.value = null
    // 保留当前选择的工作区与权限,新对话默认继承(符合"开启新对话默认为当前工作区")
    if (router.currentRoute.value.path !== '/') {
      void router.push('/').catch(() => {})
    }
    void refreshList()
  }

  async function remove(id: string) {
    await removeConversation(id)
    if (currentId.value === id) newConversation()
    await refreshList()
  }

  /** 本地重排:置顶优先 + 更新时间倒序(与后端 list 的排序保持一致) */
  function sortList() {
    list.value.sort((a, b) => {
      if (a.pinned !== b.pinned) return a.pinned ? -1 : 1
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    })
  }

  /** 重命名会话:本地先改(即时反馈),请求失败回滚 */
  async function rename(id: string, title: string) {
    const item = list.value.find((c) => c.id === id)
    const prevTitle = item?.title
    if (item) item.title = title
    try {
      await updateConversation(id, { title })
    } catch (e) {
      if (item && prevTitle !== undefined) item.title = prevTitle
      throw e
    }
  }

  /** 置顶/取消置顶:本地先改并重排,请求失败回滚 */
  async function setPinned(id: string, pinned: boolean) {
    const item = list.value.find((c) => c.id === id)
    const prevPinned = item?.pinned
    if (item) item.pinned = pinned
    sortList()
    try {
      await updateConversation(id, { pinned })
    } catch (e) {
      if (item && prevPinned !== undefined) item.pinned = prevPinned
      sortList()
      throw e
    }
  }

  /** 把当前工作区/权限/计划模式写入 localStorage,供"开启新对话默认继承上一次"使用 */
  function persistDefaults() {
    if (workspacePath.value) localStorage.setItem('llm-workspace', workspacePath.value)
    else localStorage.removeItem('llm-workspace')
    localStorage.setItem('llm-permission', permission.value)
    localStorage.setItem('llm-plan', planMode.value ? '1' : '0')
  }

  /** 切换计划模式(仅对当前会话生效;未创建的会话先落 localStorage,创建时带上) */
  async function setPlanMode(v: boolean) {
    const prev = planMode.value
    planMode.value = v
    persistDefaults()
    if (!currentId.value) return
    try {
      const conv = await updateConversation(currentId.value, { plan_mode: v })
      planMode.value = conv.plan_mode
      persistDefaults()
      await refreshList()
    } catch (e) {
      // 失败回滚,避免界面显示与实际状态不一致
      planMode.value = prev
      persistDefaults()
      throw e
    }
  }

  /** 更新会话权限(仅对当前会话生效;已存在会话保留原权限,切换影响后续步骤) */
  async function setPermission(p: Permission) {
    permission.value = p
    persistDefaults()
    if (!currentId.value) return
    try {
      const conv = await updateConversation(currentId.value, { permission: p })
      permission.value = conv.permission
      workspacePath.value = conv.workspace_path
      await refreshList()
    } catch {
      // 静默
    }
  }

  /** 更新会话工作区 */
  async function setWorkspacePath(path: string | null) {
    workspacePath.value = path
    persistDefaults()
    if (!currentId.value) return
    try {
      const conv = await setWorkspace(currentId.value, path)
      permission.value = conv.permission
      workspacePath.value = conv.workspace_path
      persistDefaults()
      await refreshList()
    } catch (e) {
      throw e
    }
  }

  /** 压缩上下文:后端生成摘要并清空 agent 记忆;本地插入摘要消息展示 */
  async function compact(): Promise<{ compacted: boolean; summary: string | null; message?: string }> {
    if (!currentId.value) return { compacted: false, summary: null, message: '当前没有对话可压缩' }
    const res = await compactConversation(currentId.value, modelStore.currentModel || '')
    if (res.compacted && res.summary) {
      messages.value.push({
        id: `compact-${Date.now()}`,
        role: 'assistant',
        content: `📌 上下文已压缩。\n\n${res.summary}`,
        created_at: new Date().toISOString(),
        meta: {},
      })
      // 压缩后上下文占用近似归零(后续对话以摘要重新开始)
      contextUsage.value.used = 0
    }
    return res
  }

  return {
    list,
    currentId,
    messages,
    pending,
    streaming,
    loading,
    permission,
    workspacePath,
    planMode,
    contextUsage,
    send,
    stop,
    approve,
    retry,
    refreshList,
    selectConversation,
    newConversation,
    remove,
    rename,
    setPinned,
    compact,
    setPermission,
    setPlanMode,
    setWorkspacePath,
  }
})
