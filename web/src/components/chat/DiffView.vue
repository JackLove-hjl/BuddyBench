<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { DiffLine } from '../../utils/diff'
import { highlightCode } from '../../utils/highlight'

/**
 * 行级 diff 视图(工具卡内嵌 / 查看变更弹窗共用)。
 *
 * - 新增/删除行按文件语言着色;行数过多时跳过 hljs(逐行解析在大 diff 下明显拖慢渲染);
 * - `follow` 用于参数流:内容增长时自动滚到底,否则长文件停在顶部看不到"正在写";
 * - `code` 挂在根节点的 data-code 上,供一键复制拿原文。
 */
const props = withDefaults(
  defineProps<{
    lines: DiffLine[]
    /** 文件语言(由路径推断;推不出来交给 hljs 自动探测) */
    lang?: string
    /** 流式中:内容增长时自动贴底 */
    follow?: boolean
    /** 一键复制用的原文 */
    code?: string
  }>(),
  { lang: '', follow: false, code: '' },
)

/** diff 行数超过这个量级就不逐行跑 hljs */
const HIGHLIGHT_MAX = 200

const rows = computed(() => {
  // 流式(follow)阶段不跑 hljs:token 每次增量都会重算全部行,大文件下逐行高亮会明显掉帧;
  // 工具跑完后 payload 到达,这里自然变成静态 diff,再一次性着色。
  const canHighlight = !props.follow && props.lines.length <= HIGHLIGHT_MAX
  return props.lines.map((line) => ({
    kind: line.kind,
    text: line.text,
    html: canHighlight ? highlightCode(line.text, props.lang, false) : '',
  }))
})

const root = ref<HTMLElement | null>(null)

async function scrollToBottom() {
  await nextTick()
  const el = root.value
  if (el) el.scrollTop = el.scrollHeight
}

// 行数变化 + 末行内容变化都要跟(同一次写入的最后一行是逐字符长出来的)
watch(
  () => [props.lines.length, props.lines[props.lines.length - 1]?.text],
  () => {
    if (props.follow) void scrollToBottom()
  },
)
onMounted(() => {
  if (props.follow) void scrollToBottom()
})
</script>

<template>
  <div ref="root" class="diff-view" :data-code="code || undefined">
    <div v-for="(row, i) in rows" :key="i" class="diff-line" :class="row.kind">
      <span class="diff-sign">{{ row.kind === 'add' ? '+' : row.kind === 'del' ? '-' : ' ' }}</span><!-- eslint-disable-next-line vue/no-v-html -- hljs 输出已转义,见 utils/highlight.ts --><span v-if="row.html" class="diff-text" v-html="row.html"></span><span v-else class="diff-text">{{ row.text }}</span>
    </div>
  </div>
</template>

<style scoped>
.diff-view {
  max-height: 360px;
  overflow: auto;
  padding: 8px 0;
  border-top: 1px solid var(--border);
  background: var(--bg-code);
  font-size: 12px;
  line-height: 1.55;
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
.diff-text {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
