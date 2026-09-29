import type { PendingSegment, ToolCall, ToolEvent } from '../../types'

/** 渲染块:正文 / 思考 / 工具卡 / 压缩提示 */
export interface RenderBlock {
  kind: 'text' | 'reasoning' | 'tool' | 'compact'
  text?: string
  call?: ToolCall
  summary?: string
}

/**
 * timeline 去重:同一张工具卡可能被记录两次。
 *
 * 后端 `bridge` 曾在 `tool_start` 时把同一张卡 append 两次(已修),但历史消息里已经
 * 落库的重复卡还在 —— 表现为「每张卡在回答末尾又重复出现一次」。同一次调用的
 * `name + started_at` 必然相同(实测两张重复卡的 started_at 一模一样),据此去重;
 * 还在生成参数、没有 started_at 的卡不参与判断,避免误删。
 */
export function dedupeToolSegments(segments: PendingSegment[]): PendingSegment[] {
  const seen = new Set<string>()
  const out: PendingSegment[] = []
  for (const seg of segments) {
    if (seg.kind === 'tool' && seg.call.started_at) {
      const key = `${seg.call.name}#${seg.call.started_at}`
      if (seen.has(key)) continue
      seen.add(key)
    }
    out.push(seg)
  }
  return out
}

/**
 * 按顺序取 segments 里**尚未用过**的同名工具卡。
 *
 * 事件流的 `tool_start` 只带被截断到 500 字符的 `args`,而 segments 里那张卡带着完整
 * payload(写类工具的 `path` / `content`、参数流 `stream`)。渲染必须复用同一个对象 ——
 * 否则卡片只剩一个工具名:路径没了、diff 也没了。
 */
function takeSegmentCard(
  segments: PendingSegment[],
  name: string,
  used: Set<ToolCall>,
): ToolCall | null {
  for (const seg of segments) {
    if (seg.kind !== 'tool' || seg.call.name !== name || used.has(seg.call)) continue
    used.add(seg.call)
    return seg.call
  }
  return null
}

/**
 * 把原始事件流合并成渲染块;事件流为空(旧数据 / 纯文本回答)时返回 null,
 * 由调用方回退到 segments 路径。
 *
 * 事件流里含正文 delta,因此能精确还原「正文 → 思考 → 工具 → 正文」的交错过程;
 * 事件流覆盖不到的工具卡(参数流阶段的卡、等审批的卡、并行调用的卡)按 timeline 顺序补到末尾。
 *
 * 为什么工具卡要复用 segments 里的对象而不是照事件流新造一个:卡上的 `payload`(写类工具的
 * path/content)与 `stream`(正在生成的那段代码)只存在于 segments 里 —— 事件流的 `args`
 * 被截断到 500 字符且不含 payload,照它新建会让卡片只剩一个工具名,路径与 diff 全丢。
 */
export function buildRenderBlocks(
  events: ToolEvent[] | undefined | null,
  segments: PendingSegment[],
): RenderBlock[] | null {
  if (!events || !events.length) return null
  const out: RenderBlock[] = []
  // 压缩发生在回答产出之前,统一置前
  for (const seg of segments) {
    if (seg.kind === 'compact') out.push({ kind: 'compact', summary: seg.summary })
  }
  const live: ToolCall[] = []
  const used = new Set<ToolCall>()
  for (const ev of events) {
    if (ev.kind === 'delta') {
      out.push({ kind: 'text', text: ev.text })
    } else if (ev.kind === 'reasoning') {
      out.push({ kind: 'reasoning', text: ev.text })
    } else if (ev.kind === 'tool_start') {
      // 优先复用 segments 里的完整卡片(带 payload / 参数流);找不到才用事件流里的残缺信息兜底
      const call = takeSegmentCard(segments, ev.name, used) ?? {
        name: ev.name,
        status: 'start' as const,
        args: ev.args,
      }
      if (call.status === 'args' || call.status === 'paused') call.status = 'start'
      live.push(call)
      out.push({ kind: 'tool', call })
    } else {
      const call = live.find((c) => c.name === ev.name && c.status === 'start')
      if (call) {
        Object.assign(call, {
          status: 'end',
          result: ev.result,
          duration_ms: ev.duration_ms,
          exit_code: ev.exit_code,
          ok: ev.ok,
        })
      }
    }
  }
  /*
   * 事件流并不覆盖 timeline 里的每一张卡:并行工具调用(同一轮同时发多个写文件)、
   * 或事件流被压缩之后,都会出现"卡片在 segments 里、却没有对应 tool_start 事件"的情况。
   * 这类卡片以前会被直接丢掉 —— 卡片连同它的 diff 在界面上凭空消失。
   * 漏掉的按 timeline 顺序补到末尾(used 保证同一张卡不会渲染两次)。
   */
  for (const seg of segments) {
    if (seg.kind === 'tool' && !used.has(seg.call)) out.push({ kind: 'tool', call: seg.call })
  }
  return out
}
