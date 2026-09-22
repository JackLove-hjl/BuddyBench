import type { PendingSegment, ToolCall, ToolEvent } from '../../types'

/** 渲染块:正文 / 思考 / 工具卡 / 压缩提示 */
export interface RenderBlock {
  kind: 'text' | 'reasoning' | 'tool' | 'compact'
  text?: string
  call?: ToolCall
  summary?: string
}

/**
 * 「正在生成参数」与「已生成完、等人工批准」的工具卡。
 *
 * 这两类卡片**只存在于 segments**,永远不会出现在事件流里 —— 后端的 `tool_args` 增量帧
 * 刻意不写进事件流:那段内容稍后由 `tool_start` 的 payload 完整落库,事件流里再存一份
 * 增量是纯冗余。
 *
 * 代价是:只要事件流非空(思考/正文的 delta 都算),渲染就走「事件流」这条路径,而它
 * 只能从 `tool_start` 造出工具卡 —— 于是"正在写的代码"这张卡在界面上根本不会出现,
 * 模型写大文件期间界面一片空白,卡片要等工具真正开始执行才冒出来。必须从 segments
 * 里把它补回来。
 */
export function liveToolCards(segments: PendingSegment[]): ToolCall[] {
  const out: ToolCall[] = []
  for (const seg of segments) {
    if (seg.kind === 'tool' && (seg.call.status === 'args' || seg.call.status === 'paused')) {
      out.push(seg.call)
    }
  }
  return out
}

/**
 * 把原始事件流合并成渲染块;事件流为空(旧数据 / 纯文本回答)时返回 null,
 * 由调用方回退到 segments 路径。
 *
 * 事件流里含正文 delta,因此能精确还原「正文 → 思考 → 工具 → 正文」的交错过程;
 * 参数流卡片不在事件流里,按"当前正在发生"追加到末尾。
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
  for (const ev of events) {
    if (ev.kind === 'delta') {
      out.push({ kind: 'text', text: ev.text })
    } else if (ev.kind === 'reasoning') {
      out.push({ kind: 'reasoning', text: ev.text })
    } else if (ev.kind === 'tool_start') {
      const call: ToolCall = { name: ev.name, status: 'start', args: ev.args }
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
  // 参数流/待批准卡片补到末尾:它们永远是"此刻正在发生"的那张
  for (const call of liveToolCards(segments)) out.push({ kind: 'tool', call })
  return out
}
