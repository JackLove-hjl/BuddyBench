<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useCodeCopy } from '../../composables/useCodeCopy'
import { resolveAssetUrl } from '../../utils/asset'
import { COPY_LABEL } from '../../utils/clipboard'
import { lineDiff } from '../../utils/diff'
import { highlightCode, langFromPath } from '../../utils/highlight'
import MarkdownContent from './MarkdownContent.vue'
import type { ToolCall } from '../../types'

const props = defineProps<{ call: ToolCall }>()

// 代码块的一键复制(事件委托,挂在卡片根节点上)
const { onCodeCopyClick } = useCodeCopy()

/** args = 参数流阶段(还没执行,正在生成正文),start = 执行中,paused = 等人工批准,end = 已结束 */
const streaming = computed(() => props.call.status === 'args')
const paused = computed(() => props.call.status === 'paused')
const running = computed(() => props.call.status === 'start')

const failed = computed(
  () =>
    props.call.ok === false ||
    (props.call.exit_code !== undefined && props.call.exit_code !== null && props.call.exit_code !== 0),
)

const durationText = computed(() => {
  const ms = props.call.duration_ms
  if (ms === undefined || ms === null) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
})

const image = computed(() => {
  if (!props.call.result) return null
  try {
    const parsed = JSON.parse(props.call.result) as { image?: string }
    if (parsed.image) return parsed.image
  } catch {
    // 非 JSON,正则兜底
  }
  const m = /\/images\/[0-9a-fA-F-]{36}\.png/.exec(props.call.result)
  return m ? m[0] : null
})

const stdout = computed(() => {
  if (!props.call.result) return ''
  try {
    const parsed = JSON.parse(props.call.result) as { stdout?: string; error?: string }
    return (parsed.stdout || '') + (parsed.error ? `\n${parsed.error}` : '')
  } catch {
    return ''
  }
})

const argsPreview = computed(() => {
  const a = props.call.args
  if (!a) return ''
  try {
    const parsed = JSON.parse(a) as { code?: string; command?: string; path?: string; content?: string }
    if (parsed.command) return parsed.command.length > 200 ? parsed.command.slice(0, 200) + '…' : parsed.command
    if (parsed.code) return parsed.code.length > 200 ? parsed.code.slice(0, 200) + '…' : parsed.code
    if (parsed.path) return parsed.path
    if (parsed.content) return parsed.content.length > 200 ? parsed.content.slice(0, 200) + '…' : parsed.content
  } catch {
    // fallthrough
  }
  return a.length > 200 ? a.slice(0, 200) + '…' : a
})

/* ---------- 参数流:模型正在生成的那段代码 ---------- */

/** 流式阶段累积的正文(按工具取对应字段) */
const streamText = computed(() => {
  const s = props.call.stream
  if (!s) return ''
  if (s.content) return s.content
  if (s.code) return s.code
  if (s.old_string || s.new_string) return [s.old_string, s.new_string].filter(Boolean).join('\n')
  return s.command || ''
})

const streamSizeText = computed(() => {
  const n = streamText.value.length
  return n < 1024 ? `${n} 字符` : `${(n / 1024).toFixed(1)} KB`
})

/* ---------- 执行阶段:完整内容 / diff ---------- */

const payload = computed(() => props.call.payload)
/** 文件路径:优先取执行阶段的载荷,其次取参数流里已解出的 path */
const filePath = computed(() => payload.value?.path || props.call.stream?.path || '')

const VERBS: Record<string, string> = {
  write_file: '写入',
  edit_file: '修改',
  run_python_code: '运行代码',
  run_shell_command: '执行命令',
}

/** 用比正文更长的围栏包住代码,避免内容里出现 ``` 时截断渲染 */
function fenced(code: string, lang: string): string {
  const longest = (code.match(/`+/g) || []).reduce((m, s) => Math.max(m, s.length), 0)
  const fence = '`'.repeat(Math.max(3, longest + 1))
  return `${fence}${lang}\n${code}\n${fence}`
}

/** 当前代码的语言:优先按文件路径推断,推不出来时交给 hljs 自动探测 */
const codeLang = computed(() => langFromPath(filePath.value))

const codeMarkdown = computed(() => {
  const code = payload.value?.content
  return code ? fenced(code, codeLang.value) : ''
})

const diffLines = computed(() =>
  props.call.name === 'edit_file' && payload.value
    ? lineDiff(payload.value.old_string || '', payload.value.new_string || '')
    : [],
)

/* ---------- 高亮 ---------- */

/** diff 行数超过这个量级就不逐行跑 hljs:大 diff 下逐行解析会明显拖慢渲染 */
const DIFF_HIGHLIGHT_MAX = 200

/** 行级高亮:diff 按文件语言着色(逐行解析,跨行结构会退化,但阅读体验明显好于纯文本) */
const diffRows = computed(() => {
  const lang = codeLang.value
  const canHighlight = diffLines.value.length <= DIFF_HIGHLIGHT_MAX
  return diffLines.value.map((line) => ({
    kind: line.kind,
    text: line.text,
    html: canHighlight ? highlightCode(line.text, lang, false) : '',
  }))
})

const command = computed(() => payload.value?.command || argsPreview.value)

/** 命令按 shell 着色(补回的 `$` 提示符一起交给 hljs,bare `$` 不会被误判) */
const commandHtml = computed(() =>
  command.value ? highlightCode(`$ ${command.value}`, 'bash', false) : '',
)

/** 参数摘要可能是命令 / 路径 / JSON 片段,语言未知 → 自动探测 */
const previewHtml = computed(() => highlightCode(argsPreview.value, '', true))

type BodyKind = 'none' | 'image' | 'code' | 'diff' | 'command' | 'preview'
const bodyKind = computed<BodyKind>(() => {
  if (image.value) return 'image'
  if (props.call.name === 'edit_file' && diffLines.value.length) return 'diff'
  if (codeMarkdown.value) return 'code'
  if (props.call.name === 'run_shell_command' && command.value) return 'command'
  if (argsPreview.value) return 'preview'
  return 'none'
})

const showBody = computed(() =>
  streaming.value || paused.value ? !!streamText.value : bodyKind.value !== 'none',
)

const hasStdout = computed(() => !image.value && !!stdout.value)

/* 参数流一出现就自动展开,让"生成过程"是看得见的;用户手动收起后不再打扰 */
const expanded = ref(false)
watch(
  () => props.call.status,
  (status) => {
    if (status === 'args') expanded.value = true
  },
  { immediate: true },
)

/* 生成中的代码块始终跟着最新一行走(否则长文件会停在顶部看不到"正在写") */
const streamEl = ref<HTMLElement | null>(null)
watch(streamText, () => {
  const el = streamEl.value
  if (el) el.scrollTop = el.scrollHeight
})

/* ---------- 流式代码的高亮(节流) ----------
 * token 增量到达非常密集,而 hljs 的耗时随已生成内容线性增长:
 * 逐帧重跑会越写越卡。这里最多每 STREAM_HIGHLIGHT_MS 重跑一次,
 * 并在状态切换(args → 执行/挂起)时立刻同步到最终内容。
 */
const STREAM_HIGHLIGHT_MS = 120

const streamHtml = ref('')
let streamTimer: number | null = null

function syncStreamHtml() {
  streamHtml.value = highlightCode(streamText.value, codeLang.value, false)
}

function cancelStreamTimer() {
  if (streamTimer !== null) {
    window.clearTimeout(streamTimer)
    streamTimer = null
  }
}

watch(streamText, () => {
  if (props.call.status !== 'args') {
    syncStreamHtml()
    return
  }
  if (streamTimer !== null) return
  streamTimer = window.setTimeout(() => {
    streamTimer = null
    syncStreamHtml()
  }, STREAM_HIGHLIGHT_MS)
})

watch(
  () => props.call.status,
  () => {
    cancelStreamTimer()
    syncStreamHtml()
  },
  { immediate: true },
)

onBeforeUnmount(cancelStreamTimer)
</script>

<template>
  <div class="tool-card" :class="{ running: running || streaming, failed }" @click="onCodeCopyClick">
    <div class="tool-head" @click="showBody && (expanded = !expanded)">
      <svg v-if="running || streaming" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="tool-spin">
        <path d="M8 2a6 6 0 1 0 6 6h-1.5A4.5 4.5 0 1 1 8 3.5V2z" />
      </svg>
      <svg v-else-if="paused" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="tool-paused">
        <path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM8 3a5 5 0 1 1 0 10A5 5 0 0 1 8 3zM6.6 5.8h1.1v4.4H6.6V5.8zm2.7 0h1.1v4.4H9.3V5.8z" />
      </svg>
      <svg v-else-if="failed" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="tool-error">
        <path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM8 3a5 5 0 1 1 0 10A5 5 0 0 1 8 3zM8 6.5a.5.5 0 0 1 .5.5v3a.5.5 0 0 1-1 0V7a.5.5 0 0 1 .5-.5zM8 11a.7.7 0 1 1 0 1.4.7.7 0 0 1 0-1.4z" />
      </svg>
      <svg v-else viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="tool-done">
        <path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM8 3a5 5 0 1 1 0 10A5 5 0 0 1 8 3zm2.35 2.29l-3.2 3.2-1.5-1.5L5 7.65l2.15 2.15 3.85-3.85-0.65-.66z" />
      </svg>
      <span class="tool-name">
        {{ VERBS[call.name] || call.name }}<template v-if="filePath"> {{ filePath }}</template>
      </span>
      <span v-if="streaming" class="tool-status running-text">正在生成 · {{ streamSizeText }}</span>
      <span v-else-if="paused" class="tool-status paused-text">待批准 · {{ streamSizeText }}</span>
      <span v-else-if="running" class="tool-status running-text">运行中…</span>
      <span v-else-if="failed" class="tool-status fail-text">失败<template v-if="props.call.exit_code !== undefined && props.call.exit_code !== null"> (exit {{ props.call.exit_code }})</template></span>
      <span v-else class="tool-status ok-text">成功</span>
      <span v-if="durationText" class="tool-duration">{{ durationText }}</span>
      <svg v-if="showBody" class="chevron" :class="{ rotated: expanded }" viewBox="0 0 16 16" width="12" height="12" fill="currentColor">
        <path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </div>

    <div v-if="expanded">
      <!-- 参数流:边生成边增长的代码(高亮按 STREAM_HIGHLIGHT_MS 节流,避免逐帧重跑) -->
      <div v-if="streaming" class="code-wrap">
        <!-- eslint-disable-next-line vue/no-v-html -- hljs 输出已转义,见 utils/highlight.ts -->
        <pre ref="streamEl" class="tool-code stream" v-html="streamHtml"></pre>
        <button type="button" class="code-copy-btn">{{ COPY_LABEL }}</button>
      </div>
      <!-- 已生成完毕、等人工批准:内容保留可审阅 -->
      <div v-else-if="paused" class="code-wrap">
        <!-- eslint-disable-next-line vue/no-v-html -- 同上 -->
        <pre class="tool-code" v-html="streamHtml"></pre>
        <button type="button" class="code-copy-btn">{{ COPY_LABEL }}</button>
      </div>

      <template v-else>
        <div v-if="bodyKind === 'image'" class="tool-image">
          <img :src="resolveAssetUrl(image!)" alt="生成图片" loading="lazy" />
        </div>
        <div v-else-if="bodyKind === 'code'" class="tool-code-block">
          <div class="tool-path">{{ filePath }}</div>
          <MarkdownContent :content="codeMarkdown" />
          <div v-if="payload?.truncated" class="tool-note">内容超过上限,仅展示前 64KB</div>
        </div>
        <pre v-else-if="bodyKind === 'diff'" class="tool-diff"><span v-for="(line, i) in diffRows" :key="i" class="diff-line" :class="line.kind"><span class="diff-sign">{{ line.kind === 'add' ? '+' : line.kind === 'del' ? '-' : ' ' }}</span><!-- eslint-disable-next-line vue/no-v-html -- hljs 输出已转义 --><span v-if="line.html" v-html="line.html"></span><template v-else>{{ line.text }}</template></span></pre>
        <div v-else-if="bodyKind === 'command'" class="code-wrap">
          <!-- eslint-disable-next-line vue/no-v-html -- hljs 输出已转义 -->
          <pre class="tool-code" :data-code="command" v-html="commandHtml"></pre>
          <button type="button" class="code-copy-btn">{{ COPY_LABEL }}</button>
        </div>
        <!-- eslint-disable-next-line vue/no-v-html -- hljs 输出已转义 -->
        <pre v-else-if="bodyKind === 'preview'" class="tool-code" v-html="previewHtml"></pre>
      </template>

      <pre v-if="hasStdout" class="tool-stdout">{{ stdout }}</pre>
    </div>
  </div>
</template>

<style scoped>
.tool-card {
  margin: 8px 0;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-elevated);
  overflow: hidden;
  cursor: default;
  transition: border-color 0.15s;
}
.tool-card.running {
  border-color: var(--accent);
}
.tool-card.failed {
  border-color: rgba(212, 61, 61, 0.5);
}
.tool-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg-reasoning);
  font-size: 13px;
  user-select: none;
}
.tool-card.running .tool-head,
.tool-card:has(.chevron) .tool-head {
  cursor: pointer;
}
.tool-name {
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12.5px;
}
.tool-status {
  margin-left: auto;
  font-size: 12px;
  flex-shrink: 0;
}
.running-text {
  color: var(--accent);
}
.paused-text {
  color: var(--text-secondary);
}
.tool-paused {
  color: var(--text-secondary);
  flex-shrink: 0;
}
.ok-text {
  color: var(--success);
}
.fail-text {
  color: var(--danger);
}
.tool-duration {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: 'SFMono-Regular', Consolas, monospace;
  flex-shrink: 0;
}
.chevron {
  color: var(--text-tertiary);
  transition: transform 0.2s;
  flex-shrink: 0;
}
.chevron.rotated {
  transform: rotate(180deg);
}
.tool-spin {
  color: var(--accent);
  animation: tool-spin 1s linear infinite;
  flex-shrink: 0;
}
@keyframes tool-spin {
  to {
    transform: rotate(360deg);
  }
}
.tool-done {
  color: var(--success);
  flex-shrink: 0;
}
.tool-error {
  color: var(--danger);
  flex-shrink: 0;
}
.tool-image {
  padding: 12px;
}
.tool-image img {
  max-width: 100%;
  border-radius: 8px;
  display: block;
}
.tool-code,
.tool-stdout {
  margin: 0;
  padding: 10px 12px;
  border-top: 1px solid var(--border);
  background: var(--bg-code);
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow: auto;
  font-family: 'SFMono-Regular', Consolas, monospace;
}
/* 生成中的代码:再高一点,并让它自己滚到底部(看得到"正在写") */
.tool-code.stream {
  max-height: 360px;
  color: var(--text);
}
.tool-stdout {
  border-top: none;
  background: var(--bg-elevated);
}
.tool-code-block {
  border-top: 1px solid var(--border);
  max-height: 420px;
  overflow: auto;
}
.tool-path {
  padding: 6px 12px;
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-reasoning);
  font-family: 'SFMono-Regular', Consolas, monospace;
  position: sticky;
  top: 0;
}
.tool-note {
  padding: 4px 12px 8px;
  font-size: 11px;
  color: var(--danger);
}
.tool-diff {
  margin: 0;
  padding: 8px 0;
  border-top: 1px solid var(--border);
  background: var(--bg-code);
  font-size: 12px;
  line-height: 1.55;
  max-height: 360px;
  overflow: auto;
  font-family: 'SFMono-Regular', Consolas, monospace;
}
.diff-line {
  display: block;
  padding: 0 12px;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--text-secondary);
}
.diff-line.add {
  background: rgba(62, 207, 142, 0.12);
  color: var(--text);
}
.diff-line.del {
  background: rgba(212, 61, 61, 0.12);
  color: var(--text);
}
.diff-sign {
  display: inline-block;
  width: 12px;
  color: var(--text-tertiary);
  user-select: none;
}
</style>
