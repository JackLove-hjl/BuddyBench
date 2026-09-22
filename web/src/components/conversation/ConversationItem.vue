<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { NDropdown, NInput, useDialog } from 'naive-ui'
import type { Conversation } from '../../types'

const props = defineProps<{
  conversation: Conversation
  active: boolean
}>()

const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'remove', id: string): void
  (e: 'rename', id: string, title: string): void
  (e: 'pin', id: string, pinned: boolean): void
}>()

const dialog = useDialog()
const hover = ref(false)
const menuOpen = ref(false)
const editing = ref(false)
const draft = ref('')

const DANGER_COLOR = 'var(--danger)'

// 下拉项图标(16 网格):用 currentColor 填充,便于跟随文字着色
const ICON_EDIT = [
  'M12.854.146a.5.5 0 0 0-.707 0L10.5 1.793 14.207 5.5l1.647-1.646a.5.5 0 0 0 0-.708l-3-3z',
  'M15.5 6.207 9.793 2.5 3.293 9H3.5a.5.5 0 0 1 .5.5v.5h.5a.5.5 0 0 1 .5.5v.5h.5a.5.5 0 0 1 .5.5v.5h.5a.5.5 0 0 1 .5.5v.207l6.5-6.5z',
  'M8.032 13.675A.5.5 0 0 1 6 13.5V13h-.5a.5.5 0 0 1-.5-.5V12h-.5a.5.5 0 0 1-.5-.5V11h-.5a.5.5 0 0 1-.5-.5V10h-.5a.5.5 0 0 1-.175-.032l-.179.178a.5.5 0 0 0-.11.168l-2 5a.5.5 0 0 0 .65.65l5-2a.5.5 0 0 0 .168-.11l.178-.178z',
]
const ICON_PIN = [
  'M8 1.5a4.5 4.5 0 0 0-4.5 4.5c0 3.2 4.5 8.5 4.5 8.5s4.5-5.3 4.5-8.5A4.5 4.5 0 0 0 8 1.5zm0 6.2a1.7 1.7 0 1 1 0-3.4 1.7 1.7 0 0 1 0 3.4z',
]
const ICON_TRASH = [
  'M6.5 7a.5.5 0 0 1 .5.5v3a.5.5 0 0 1-1 0v-3a.5.5 0 0 1 .5-.5zm3 0a.5.5 0 0 1 .5.5v3a.5.5 0 0 1-1 0v-3a.5.5 0 0 1 .5-.5z',
  'M3 4.5a.5.5 0 0 1 .5-.5h9a.5.5 0 0 1 0 1h-.45l-.59 7.1A1.5 1.5 0 0 1 10.47 13.5h-4.94a1.5 1.5 0 0 1-1.49-1.4L3.45 5H3a.5.5 0 0 1-.5-.5zM6 4V3h4v1H6z',
]

/** 生成下拉项图标:传 color 时连同图标一起着色(删除项用红色) */
function menuIcon(paths: string[], color?: string) {
  return () =>
    h(
      'svg',
      {
        viewBox: '0 0 16 16',
        width: 15,
        height: 15,
        fill: 'currentColor',
        style: color ? { color } : undefined,
      },
      paths.map((d) => h('path', { d })),
    )
}

/** 鼠标移入时右侧「⋯」的下拉菜单(删除项用红色文字 + 红色图标) */
const menuOptions = computed(() => [
  { label: '编辑名称', key: 'rename', icon: menuIcon(ICON_EDIT) },
  {
    label: props.conversation.pinned ? '取消置顶' : '置顶',
    key: 'pin',
    icon: menuIcon(ICON_PIN),
  },
  {
    // label 用渲染函数才能给文字单独上红色(样式表里的默认色优先级高于父级内联色)
    label: () => h('span', { style: { color: DANGER_COLOR } }, '删除'),
    key: 'remove',
    icon: menuIcon(ICON_TRASH, DANGER_COLOR),
  },
])

function onMenuSelect(key: string) {
  menuOpen.value = false
  if (key === 'rename') startEdit()
  else if (key === 'pin') emit('pin', props.conversation.id, !props.conversation.pinned)
  else if (key === 'remove') confirmRemove()
}

/** 删除前二次确认(下拉菜单里挂 popconfirm 会互相打架,统一用对话框) */
function confirmRemove() {
  dialog.warning({
    title: '删除会话',
    content: `确定删除「${props.conversation.title}」?该会话的历史消息将一并删除。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () => emit('remove', props.conversation.id),
  })
}

/** 内联重命名:进入编辑态,输入框 autofocus 落在原名称上 */
function startEdit() {
  draft.value = props.conversation.title
  editing.value = true
}

/** 提交重命名:空串或没改动都视为取消 */
function commitEdit() {
  if (!editing.value) return
  editing.value = false
  const title = draft.value.trim()
  if (!title || title === props.conversation.title) return
  emit('rename', props.conversation.id, title)
}

/** Esc 取消:先退出编辑态,避免随后的 blur 再提交一次 */
function cancelEdit() {
  editing.value = false
  draft.value = props.conversation.title
}

function onClick() {
  if (editing.value) return
  emit('select', props.conversation.id)
}
</script>

<template>
  <div
    class="conv-item"
    :class="{ active, editing }"
    @mouseenter="hover = true"
    @mouseleave="hover = false"
    @click="onClick"
  >
    <n-input
      v-if="editing"
      v-model:value="draft"
      class="conv-title-input"
      size="tiny"
      autofocus
      :maxlength="200"
      @click.stop
      @keydown.enter.prevent="commitEdit"
      @keydown.esc.prevent="cancelEdit"
      @blur="commitEdit"
    />
    <template v-else>
      <span v-if="conversation.pinned" class="conv-pin" title="已置顶">
        <svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor">
          <path d="M8 1.5a4.5 4.5 0 0 0-4.5 4.5c0 3.2 4.5 8.5 4.5 8.5s4.5-5.3 4.5-8.5A4.5 4.5 0 0 0 8 1.5zm0 6.2a1.7 1.7 0 1 1 0-3.4 1.7 1.7 0 0 1 0 3.4z" />
        </svg>
      </span>
      <span class="conv-title">{{ conversation.title }}</span>
      <span v-show="!hover && !menuOpen" class="conv-count">{{ conversation.message_count ?? 0 }}</span>
      <n-dropdown
        trigger="click"
        placement="bottom-end"
        :options="menuOptions"
        @select="onMenuSelect"
        @update:show="(v: boolean) => (menuOpen = v)"
      >
        <button v-show="hover || menuOpen" class="conv-more" title="更多操作" @click.stop>
          <svg viewBox="0 0 16 16" width="15" height="15" fill="currentColor">
            <circle cx="8" cy="3" r="1.4" />
            <circle cx="8" cy="8" r="1.4" />
            <circle cx="8" cy="13" r="1.4" />
          </svg>
        </button>
      </n-dropdown>
    </template>
  </div>
</template>

<style scoped>
.conv-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--text);
  transition: background 0.12s;
  font-size: 14px;
}
.conv-item:hover {
  background: var(--bg-hover);
}
.conv-item.active {
  background: var(--bg-active);
}
.conv-item.editing {
  cursor: default;
}
.conv-title {
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.conv-title-input {
  flex: 1;
  min-width: 0;
}
.conv-pin {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  color: var(--accent);
}
.conv-count {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-elevated);
  border-radius: 10px;
  padding: 0 6px;
  line-height: 16px;
}
.conv-more {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  border-radius: 6px;
  cursor: pointer;
  padding: 0;
}
.conv-more:hover {
  color: var(--text);
  background: var(--bg-elevated);
}
</style>
