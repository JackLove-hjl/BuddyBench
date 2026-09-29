/** 领域模型与 SSE 事件类型(对齐 backend API 契约) */

export type Permission = 'read_only' | 'workspace_writable' | 'full_access'

export const PERMISSION_READ_ONLY: Permission = 'read_only'
export const PERMISSION_WORKSPACE_WRITABLE: Permission = 'workspace_writable'
export const PERMISSION_FULL_ACCESS: Permission = 'full_access'

export const PERMISSION_LABELS: Record<Permission, string> = {
  read_only: '只读',
  workspace_writable: '工作区可写',
  full_access: '全部权限',
}

export interface ModelInfo {
  id: string
  provider: string
  display_name: string
  available: boolean
  /** 输入上下文上限(上下文使用率圆圈的分母);未配置为 null */
  input_tokens?: number | null
  /** 最大输出 token 数 */
  output_tokens?: number | null
}

export interface Conversation {
  id: string
  title: string
  permission: Permission
  workspace_path: string | null
  /** 计划模式:开启后模型先探索并提交计划,经批准后才实施 */
  plan_mode: boolean
  /** 置顶:左侧列表排序优先 */
  pinned: boolean
  summary: string | null
  created_at: string
  updated_at: string
  message_count?: number
}

/** 写类工具的完整展示载荷(见后端 agent/tool_display.py);`args` 只有 500 字符,不够看真正写进去的内容 */
export interface ToolPayload {
  path?: string
  content?: string
  old_string?: string
  new_string?: string
  /** run_shell_command 的命令行 */
  command?: string
  /** 内容超过后端上限,已截断 */
  truncated?: boolean
}

export interface ToolCall {
  name: string
  /** args = 正在生成参数,start = 执行中,end = 已结束,paused = 已生成完毕但等人工批准 */
  status: 'args' | 'start' | 'end' | 'paused'
  args?: string
  result?: string
  /** 参数流阶段累积的字段文本(path / content / old_string / new_string) */
  stream?: Record<string, string>
  /** 执行开始时后端给的完整载荷 */
  payload?: ToolPayload
  /** 增强字段:开始时间戳(ms)、耗时(ms)、退出码、是否成功 */
  started_at?: number
  duration_ms?: number
  exit_code?: number | null
  ok?: boolean
}

export interface ToolResultJson {
  status?: string
  image?: string
  stdout?: string
  error?: string | null
  exit_code?: number | null
}

/** 待人工审批的危险操作(「工作区可写」下,执行前挂起) */
export interface ApprovalAction {
  name: string
  args?: Record<string, unknown>
  description?: string
}

/** ask_user 的候选项(计划模式下用「选择引导 + 自定义输入」收集需求) */
export interface AskUserOption {
  label: string
  description?: string
}

/** ask_user 的单个问题;options 为空时表示只能手写回答 */
export interface AskUserQuestion {
  question: string
  options?: AskUserOption[]
}

/** 按真实执行顺序排列的流式片段(思考块 / 工具调用 / 上下文压缩),用于交错渲染 */
export type PendingSegment =
  | { kind: 'reasoning'; content: string }
  | { kind: 'tool'; call: ToolCall }
  | { kind: 'compact'; summary: string }

/**
 * 原始事件流条目(后端已把同类增量合并,因此条目数≈段数而非 token 数)。
 * 相比 segments 多出正文 delta,可精确还原「正文 → 思考 → 工具 → 正文」的交错顺序。
 */
export type ToolEvent =
  | { at: number; kind: 'delta'; text: string }
  | { at: number; kind: 'reasoning'; text: string }
  | { at: number; kind: 'tool_start'; name: string; args?: string }
  | {
      at: number
      kind: 'tool_end'
      name: string
      result?: string
      duration_ms?: number
      exit_code?: number | null
      ok?: boolean
    }

export interface Usage {
  prompt_tokens?: number
  completion_tokens?: number
}

export interface MessageMeta {
  images?: string[]
  tool_calls?: ToolCall[]
  /** 完整工具执行记录(含耗时/状态/参数/结果),刷新后回放用 */
  tool_records?: ToolCall[]
  /** 按真实执行顺序的时间线(思考块/工具卡/压缩),刷新后保持交错渲染 */
  segments?: PendingSegment[]
  /** 原始事件流(增量合并后),用于精确重建交错顺序 */
  events?: ToolEvent[]
  /** 待人工审批的动作(刷新后仍可继续批准/拒绝) */
  pending_approval?: { actions: ApprovalAction[] }
  usage?: Usage
  error?: string
  reasoning?: string
  attachments?: { url: string; filename: string; kind: 'image' | 'file' }[]
  /**
   * 本轮干活用时(ms),由后端在收尾消息上落库 → 刷新后回复底部的"用时 X 秒"仍在。
   * 等待用户审批/回答的那段时间不计入(那期间后端没有请求在跑)。
   */
  duration_ms?: number
  /** 中途挂起(等审批)的消息上记的"已干活用时",供续跑时续算,前端不展示 */
  active_ms?: number
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  model?: string | null
  meta: MessageMeta
  created_at: string
}

/** 流式中唯一的可变化对象(未固化进 messages) */
export interface PendingMessage {
  status: 'streaming' | 'error' | 'interrupted'
  content: string
  reasoning: string
  toolCalls: ToolCall[]
  /** 按真实执行顺序的片段列表,驱动思考块与工具卡交错渲染 */
  segments: PendingSegment[]
  images: string[]
  usage: Usage
  model?: string
  error?: string
  /** 原始事件流(流式期间累积,最终落库为 meta.events) */
  events?: ToolEvent[]
  /** 待审批动作:非空时表示本轮被挂起,等待用户批准/拒绝 */
  approval?: ApprovalAction[] | null
}

export type SSEEventType =
  | 'delta'
  | 'reasoning'
  | 'tool'
  | 'tool_args'
  | 'compact'
  | 'approval'
  | 'done'
  | 'error'

export interface SSEBaseEvent {
  type: SSEEventType
}

export interface SSEDeltaEvent extends SSEBaseEvent {
  type: 'delta'
  content: string
}

export interface SSEReasoningEvent extends SSEBaseEvent {
  type: 'reasoning'
  content: string
}

export interface SSEToolEvent extends SSEBaseEvent {
  type: 'tool'
  name: string
  status: 'start' | 'end'
  args?: string
  result?: string
  /** 写类工具的完整展示载荷(start 帧携带) */
  payload?: ToolPayload
  started_at?: number
  duration_ms?: number
  exit_code?: number | null
  ok?: boolean
}

export interface SSEDoneEvent extends SSEBaseEvent {
  type: 'done'
  message_id: string
  model: string
  images: string[]
  usage: Usage
  /** 模型上下文窗口大小(用于前端上下文使用率圆圈) */
  context_window?: number
  /** 本轮干活用时(ms):显示在回复底部"用时 X 秒" */
  duration_ms?: number
}

export interface SSEErrorEvent extends SSEBaseEvent {
  type: 'error'
  code: string
  message: string
}

/** 上下文压缩(自动预判 / 溢出恢复) */
export interface SSECompactEvent extends SSEBaseEvent {
  type: 'compact'
  ok: boolean
  summary: string
  message: string
}

/** 工具调用的参数增量:模型"正在写的那段代码" */
export interface SSEToolArgsEvent extends SSEBaseEvent {
  type: 'tool_args'
  name: string
  /** 增量所属字段:path / content / old_string / new_string;null 表示只是进度帧 */
  field: string | null
  delta: string
  /** 该字段已生成的总字符数 */
  size: number
}

/** 危险操作挂起,等待人工审批 */
export interface SSEApprovalEvent extends SSEBaseEvent {
  type: 'approval'
  actions: ApprovalAction[]
}

export type SSEEvent =
  | SSEDeltaEvent
  | SSEReasoningEvent
  | SSEToolEvent
  | SSEToolArgsEvent
  | SSECompactEvent
  | SSEApprovalEvent
  | SSEDoneEvent
  | SSEErrorEvent

export interface SSEErrorMeta {
  /** 非 2xx 前置校验失败时的 HTTP 状态码(流未开始) */
  httpStatus?: number
}

export interface SSEHandlers {
  onDelta?: (content: string) => void
  onReasoning?: (content: string) => void
  onTool?: (ev: SSEToolEvent) => void
  onToolArgs?: (ev: SSEToolArgsEvent) => void
  onCompact?: (ev: SSECompactEvent) => void
  onApproval?: (ev: SSEApprovalEvent) => void
  onDone?: (ev: SSEDoneEvent) => void
  onError?: (ev: SSEErrorEvent, meta?: SSEErrorMeta) => void
}
