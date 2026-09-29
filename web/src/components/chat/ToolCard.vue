<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useCodeCopy } from '../../composables/useCodeCopy'
import { useImagePreview } from '../../composables/useImagePreview'
import { useSidePanel } from '../../composables/useSidePanel'
import { useConversationStore } from '../../stores/conversation'
import { resolveAssetUrl } from '../../utils/asset'
import { COPY_LABEL } from '../../utils/clipboard'
import { diffStats, lineDiff, newFileDiff } from '../../utils/diff'
import { highlightCode, langFromPath } from '../../utils/highlight'
import DiffView from './DiffView.vue'
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
  // 兜底:computer 的屏幕截图是 .jpg(见 computer_driver),不能只认 .png
  const m = /\/images\/[0-9a-fA-F-]{36}\.(png|jpe?g|webp)/.exec(props.call.result)
  return m ? m[0] : null
})

/** 工具产出的图片(computer 的屏幕截图 / 绘图)以缩略图展示,点开进全站放大浮层 */
const { openImage } = useImagePreview()
const absoluteImage = computed(() => (image.value ? resolveAssetUrl(image.value) : ''))
const imageLabel = computed(() => (props.call.name === 'computer' ? '屏幕截图' : '图片'))

const stdout = computed(() => {
  if (!props.call.result) return ''
  try {
    const parsed = JSON.parse(props.call.result) as { stdout?: string; error?: string }
    return (parsed.stdout || '') + (parsed.error ? `\n${parsed.error}` : '')
  } catch {
    return ''
  }
})

/** computer 工具的动作摘要:click (10, 20) / type "…" / keypress ctrl+c(截 200 字符内) */
function computerPreview(parsed: Record<string, unknown>): string {
  const action = String(parsed.action || '')
  const clip = (s: string) => (s.length > 60 ? s.slice(0, 60) + '…' : s)
  if (action === 'type') return `type "${clip(String(parsed.text || ''))}"`
  if (action === 'keypress') {
    const keys = Array.isArray(parsed.keys) ? (parsed.keys as string[]).join('+') : ''
    return `keypress ${keys}`
  }
  if (action === 'scroll') return `scroll (${parsed.scroll_x ?? 0}, ${parsed.scroll_y ?? 0})`
  if (action === 'drag') return `drag ${JSON.stringify(parsed.path ?? [])}`
  if (action === 'wait') return `wait ${parsed.seconds ?? ''}s`
  if (parsed.x !== undefined && parsed.y !== undefined) return `${action} (${parsed.x}, ${parsed.y})`
  return action
}

const argsPreview = computed(() => {
  const a = props.call.args
  if (!a) return ''
  try {
    const parsed = JSON.parse(a) as {
      code?: string
      command?: string
      path?: unknown
      content?: string
      action?: string
    }
    if (parsed.action) return computerPreview(parsed)
    if (parsed.command) return parsed.command.length > 200 ? parsed.command.slice(0, 200) + '…' : parsed.command
    if (parsed.code) return parsed.code.length > 200 ? parsed.code.slice(0, 200) + '…' : parsed.code
    // 只有字符串 path 才当路径(computer 的 drag 用数组 path,不能原样当预览)
    if (typeof parsed.path === 'string') return parsed.path
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

/** 卡片标题:edit_file 前面不带工具名,直接给文件路径;拿不到路径时回退工具名 */
const cardTitle = computed(() => {
  const path = filePath.value
  if (props.call.name === 'edit_file') return path || props.call.name
  return path ? `${props.call.name} ${path}` : props.call.name
})

/* 写入/编辑文件的卡:点击打开右侧栏看"改完的完整文件"(不再展开卡片本体) */
const { openFile } = useSidePanel()
const convStore = useConversationStore()

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

/* ---------- 代码变更(diff) ----------
 * 写文件 / 改文件都以 diff 呈现:新建文件整篇是新增(+),修改文件按 old → new 对比。
 */
const diffLines = computed(() => {
  const p = payload.value
  if (!p) return []
  if (props.call.name === 'edit_file') return lineDiff(p.old_string || '', p.new_string || '')
  if (props.call.name === 'write_file') return newFileDiff(p.content || '')
  return []
})

const stats = computed(() => diffStats(diffLines.value))
const hasDiff = computed(() => diffLines.value.length > 0)

// 卡片里展示**完整** diff(与「查看变更」弹窗同源):早先按行数截断过,但用户看到的
// 就是"内容被截断了" —— 行数上限交给 DiffView 的 max-height + 滚动,不再砍内容。
// 唯一可能变短的情况是后端 payload 自身超限(64KB),那时另有提示。

/** 参数流阶段"正在写的代码":新建文件按新增行实时长出来 */
const streamDiffLines = computed(() =>
  props.call.name === 'write_file' ? newFileDiff(streamText.value) : [],
)

/** 卡片根节点:点「查看变更」就地展开后,把它滚进视野 */
const cardEl = ref<HTMLElement | null>(null)

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
  if (hasDiff.value) return 'diff'
  if (codeMarkdown.value) return 'code'
  if (props.call.name === 'run_shell_command' && command.value) return 'command'
  if (argsPreview.value) return 'preview'
  return 'none'
})

const showBody = computed(() =>
  streaming.value || paused.value ? !!streamText.value : bodyKind.value !== 'none',
)

const hasStdout = computed(() => !image.value && !!stdout.value)

/* 卡片**默认折叠**。唯一自动展开的情况是"新建文件正在生成" —— 那是模型从零写整篇,
 * 折叠着界面一片空白;而 `edit_file` 的参数流是老/新两段文本拼在一起,展开反而更乱,
 * 同样保持折叠(与写完/执行完的卡一致),由用户点开或点「查看变更」去看。
 * 用户手动收起后不再打扰。 */
const AUTO_EXPAND_TOOLS = new Set(['write_file'])
const expanded = ref(false)
const userCollapsed = ref(false)
function toggle() {
  expanded.value = !expanded.value
  userCollapsed.value = !expanded.value
}

/**
 * 「查看变更」:**就地展开卡片**,不再另开弹窗。
 *
 * 卡片正文展示的就是完整 diff(见上面的 diffLines / DiffView),弹窗只是把同一份内容
 * 又浮一层 —— 现在直接在卡片下方展开,展开后把它滚进视野(卡片可能已经在屏幕外)。
 */
function toggleDiff() {
  toggle()
  if (!expanded.value) return
  void nextTick(() => cardEl.value?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
}

/**
 * 点卡片的行为:
 * - 写入 / 编辑文件的卡(且已跑完)→ 打开右侧栏看**改完的完整文件**(不限代码文件,
 *   普通文本 / 配置 / 数据文件同理);参数流阶段文件还没落盘,仍按展开/收起处理。
 * - 其余卡 → 展开 / 收起。
 */
const canOpenFile = computed(() => !!filePath.value && props.call.status !== 'args')

function onCardClick() {
  if (canOpenFile.value) {
    void openFile({
      path: filePath.value,
      workspaceRoot: convStore.workspacePath,
      conversationId: convStore.currentId,
      // 读不到工作区文件时的兜底:write_file 的 payload 就是写进去的全文
      fallback: payload.value?.content || '',
    })
    return
  }
  if (showBody.value) toggle()
}

watch(
  () => props.call.status,
  (status) => {
    if (userCollapsed.value) return
    if (status === 'args' && AUTO_EXPAND_TOOLS.has(props.call.name)) expanded.value = true
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
  <div ref="cardEl" class="tool-card" :class="{ running: running || streaming, failed }" @click="onCodeCopyClick">
    <div class="tool-head" :class="{ clickable: showBody || canOpenFile }" @click="onCardClick">
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
      <!-- 工具英文名 + 文件路径;edit_file 不带工具名,直接给路径 -->
      <span class="tool-name">{{ cardTitle }}</span>
      <span v-if="hasDiff" class="tool-diffstats">
        <span class="stat-add">+{{ stats.added }}</span>
        <span class="stat-del">−{{ stats.removed }}</span>
      </span>
      <button
        v-if="hasDiff"
        type="button"
        class="tool-view-diff"
        :title="expanded ? '收起差异内容' : '在卡片下方展开完整差异'"
        @click.stop="toggleDiff()"
      >
        {{ expanded ? '收起变更' : '查看变更' }}
      </button>
      <span v-if="streaming" class="tool-status running-text">正在生成 · {{ streamSizeText }}</span>
      <span v-else-if="paused" class="tool-status paused-text">待批准 · {{ streamSizeText }}</span>
      <span v-else-if="running" class="tool-status running-text">运行中…</span>
      <span v-else-if="failed" class="tool-status fail-text">失败<template v-if="props.call.exit_code !== undefined && props.call.exit_code !== null"> (exit {{ props.call.exit_code }})</template></span>
      <span v-else class="tool-status ok-text">成功</span>
      <span v-if="durationText" class="tool-duration">{{ durationText }}</span>
      <!-- 文件卡用"打开右侧栏"的图标;其余卡仍是展开/收起箭头 -->
      <svg v-if="canOpenFile" class="open-icon" viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M9.5 3.5h3v3M12.3 3.7 7.8 8.2" />
        <path d="M12.5 10.5v2a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1h2" />
      </svg>
      <svg v-else-if="showBody" class="chevron" :class="{ rotated: expanded }" viewBox="0 0 16 16" width="12" height="12" fill="currentColor">
        <path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </div>

    <div v-if="expanded">
      <!-- 参数流:边生成边增长的代码(新建文件直接按 diff 的新增行实时长出来) -->
      <div v-if="streaming" class="code-wrap">
        <DiffView
          v-if="streamDiffLines.length"
          :lines="streamDiffLines"
          :lang="codeLang"
          :code="streamText"
          follow
        />
        <!-- eslint-disable-next-line vue/no-v-html -- hljs 输出已转义,见 utils/highlight.ts -->
        <pre v-else ref="streamEl" class="tool-code stream" v-html="streamHtml"></pre>
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
          <button
            type="button"
            class="tool-thumb"
            :title="`${imageLabel} · 点击放大`"
            @click.stop="openImage(absoluteImage, imageLabel)"
          >
            <img :src="absoluteImage" :alt="imageLabel" loading="lazy" />
            <span class="tool-thumb-hint">点击放大</span>
          </button>
        </div>
        <div v-else-if="bodyKind === 'code'" class="tool-code-block">
          <div class="tool-path">{{ filePath }}</div>
          <MarkdownContent :content="codeMarkdown" />
          <div v-if="payload?.truncated" class="tool-note">内容超过上限,仅展示前 64KB</div>
        </div>
        <div v-else-if="bodyKind === 'diff'" class="tool-diff-wrap">
          <DiffView :lines="diffLines" :lang="codeLang" :code="payload?.new_string ?? payload?.content ?? ''" />
          <div v-if="payload?.truncated" class="tool-note">内容超过上限,仅展示前 64KB</div>
        </div>
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
/* 可点击(能展开,或能打开右侧文件栏) */
.tool-head.clickable {
  cursor: pointer;
}
.open-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
}
.tool-head.clickable:hover .open-icon {
  color: var(--accent);
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
/* 缩略图:整张可见但只占一小块;点击进放大浮层看细节(见 ImagePreview.vue) */
.tool-thumb {
  position: relative;
  display: inline-block;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  line-height: 0;
  overflow: hidden;
  cursor: zoom-in;
}
.tool-thumb img {
  display: block;
  max-width: 100%;
  max-height: 160px;
  width: auto;
  border-radius: 7px;
}
.tool-thumb:hover {
  border-color: var(--border-strong);
}
.tool-thumb-hint {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 2px 6px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
  line-height: 1.4;
  opacity: 0;
  transition: opacity 0.15s;
  pointer-events: none;
}
.tool-thumb:hover .tool-thumb-hint {
  opacity: 1;
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
/* 变更统计 + 查看完整 diff(diff 本体样式在 DiffView.vue) */
.tool-diffstats {
  display: flex;
  gap: 6px;
  font-size: 12px;
  font-family: 'SFMono-Regular', Consolas, monospace;
  flex-shrink: 0;
}
.stat-add {
  color: var(--success);
}
.stat-del {
  color: var(--danger);
}
.tool-view-diff {
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
  transition: border-color 0.15s, color 0.15s;
}
.tool-view-diff:hover {
  border-color: var(--accent);
  color: var(--accent);
}
</style>
