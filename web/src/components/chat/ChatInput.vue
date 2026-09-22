<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { NPopover, useMessage } from 'naive-ui'
import ModelSelect from '../common/ModelSelect.vue'
import PermissionSelect from './PermissionSelect.vue'
import WorkspaceSelect from './WorkspaceSelect.vue'
import ContextRing from './ContextRing.vue'
import CommandMenu from './CommandMenu.vue'
import { filterCommands, type CommandDef } from './commands'
import FileMention from './FileMention.vue'
import { useConversationStore } from '../../stores/conversation'
import { useModelStore } from '../../stores/model'
import { readTextFile, uploadFile } from '../../api/uploads'
import { resolveAssetUrl } from '../../utils/asset'

export interface ChatAttachment {
  url: string
  filename: string
  kind: 'image' | 'file'
  content?: string
  localPreview?: string // 本地预览 URL(未上传时)
}

defineProps<{ streaming: boolean }>()
const emit = defineEmits<{
  (e: 'send', text: string, attachments: ChatAttachment[]): void
  (e: 'stop'): void
}>()

const message = useMessage()
const convStore = useConversationStore()
const modelStore = useModelStore()
/** 上下文圆圈分母:优先用当前模型声明的输入上下文,其次回退后端下发的窗口值 */
const contextWindow = computed(
  () => modelStore.currentModelInfo?.input_tokens || convStore.contextUsage.window,
)
const value = ref('')
const composing = ref(false)
const textareaEl = ref<HTMLTextAreaElement | null>(null)
const fileInputEl = ref<HTMLInputElement | null>(null)
const attachMenuOpen = ref(false)
const attachments = ref<ChatAttachment[]>([])
const uploading = ref(false)

// /命令 与 @文件 触发状态
const cmdVisible = ref(false)
const cmdQuery = ref('')
const mentionVisible = ref(false)

const MAX_LEN = 20000
const LINE_HEIGHT = 24
const MAX_ROWS = 8

const commands = computed<CommandDef[]>(() => [
  {
    key: 'plan',
    label: '计划模式',
    description: '给本条消息挂上 /plan:先探索并给出计划,经你批准后再实施',
    run: () => {
      addPlanToken()
      // 关掉命令浮层,否则紧接着的 Enter 会再次命中同一条命令
      cmdVisible.value = false
    },
  },
  {
    key: 'compact',
    label: '压缩上下文',
    description: '将对话历史压缩为摘要,释放上下文窗口',
    run: () => {
      if (!convStore.currentId) {
        message.warning('当前没有对话可压缩')
        return
      }
      void convStore
        .compact()
        .then((res) => {
          if (res.compacted) message.success('上下文已压缩')
          else if (res.message) message.info(res.message)
        })
        .catch((e) => message.error((e as Error).message))
      clearText()
    },
  },
])

/** 计划模式令牌:输入框内的 /plan 挂件(挂上=本条消息走计划;点掉=取消) */
const planToken = ref(false)
/** 手敲的 /plan 前缀(没走命令菜单时)与令牌等价,发送前一并剥离。要求后面是空白或结尾,免得把 `/plan/tmp` 这类路径当成前缀 */
const PLAN_PREFIX_RE = /^\s*\/plan(?=\s|$)\s*/i

/**
 * 令牌可见 = 本条消息挂了令牌,或会话本身已处于计划模式(计划未批准前)。
 * 后者语义相同:下一条消息同样要过规划,所以照样显示成令牌,点掉即可退出。
 */
const showPlanToken = computed(() => planToken.value || convStore.planMode)

const placeholder = computed(() =>
  showPlanToken.value || PLAN_PREFIX_RE.test(value.value)
    ? '描述你的任务以生成计划'
    : '给 BuddyBench 发送消息(/ 唤起命令,@ 引用文件,支持直接粘贴图片或文档)',
)

/** 选中计划模式:挂上令牌(不写进 textarea,所以 placeholder 仍然可见),光标交给输入框 */
function addPlanToken() {
  planToken.value = true
  // 手敲 /plan 再回车选中命令时,把文本里的前缀清掉,避免和令牌重复
  if (PLAN_PREFIX_RE.test(value.value)) {
    value.value = value.value.replace(PLAN_PREFIX_RE, '')
  }
  void nextTick(() => {
    autoResize()
    textareaEl.value?.focus()
  })
}

/** 点掉令牌:取消本条消息的计划;若会话已处于计划模式,一并退出,避免状态残留 */
async function removePlanToken() {
  planToken.value = false
  if (!convStore.planMode) return
  try {
    await convStore.setPlanMode(false)
  } catch (e) {
    message.error((e as Error).message)
  }
}

function autoResize() {
  const el = textareaEl.value
  if (!el) return
  el.style.height = 'auto'
  const maxHeight = LINE_HEIGHT * MAX_ROWS
  el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`
}

function clearText() {
  value.value = ''
  cmdVisible.value = false
  mentionVisible.value = false
  requestAnimationFrame(autoResize)
}

function onKeydown(e: KeyboardEvent) {
  // 输入 @ 打开工作区文件选择(仅当位于行首或空格后;需先选工作区)
  if (e.key === '@' && !composing.value) {
    const cursor = textareaEl.value?.selectionStart ?? 0
    const before = value.value.slice(0, cursor)
    if (!before || /\s$/.test(before)) {
      // 有工作区即可 @(新对话尚未创建会话但已从 localStorage 继承工作区时也可)
      if (!convStore.workspacePath) {
        message.warning('请先在输入框上方选择工作区,再使用 @ 引用文件')
        return
      }
      mentionVisible.value = true
      cmdVisible.value = false
    }
  }
  // 行首输入 / 打开命令菜单
  if (e.key === '/' && !composing.value) {
    const cursor = textareaEl.value?.selectionStart ?? 0
    const before = value.value.slice(0, cursor)
    if (!before) {
      cmdVisible.value = true
      cmdQuery.value = ''
      mentionVisible.value = false
    }
  }
  if (e.key === 'Escape') {
    cmdVisible.value = false
    mentionVisible.value = false
    attachMenuOpen.value = false
  }
  if (e.key === 'Enter' && !e.shiftKey && !composing.value) {
    if (cmdVisible.value) {
      // 命令菜单打开时 Enter 选中"当前过滤结果的第一条",与菜单里看到的保持一致
      const first = filterCommands(commands.value, cmdQuery.value)[0]
      if (first) {
        onCmdSelect(first) // 与点菜单同一入口:顺手清掉唤起用的 / 与查询串
        return
      }
      cmdVisible.value = false // 无匹配命令:关掉菜单,把输入当普通消息发出
    }
    e.preventDefault()
    submit()
  }
  if (!cmdVisible.value && !mentionVisible.value) return
  // 关闭浮层后继续处理
  if (e.key === 'Backspace' && value.value.length <= 1) {
    cmdVisible.value = false
    mentionVisible.value = false
  }
}

function onInput() {
  // 输入过程中更新命令查询
  if (cmdVisible.value) {
    const cursor = textareaEl.value?.selectionStart ?? 0
    const before = value.value.slice(0, cursor)
    const match = before.match(/\/([a-z]*)$/i)
    cmdQuery.value = match ? match[1] : ''
    if (!match) cmdVisible.value = false
  }
  autoResize()
}

/**
 * 清掉唤起菜单用的「/ + 查询串」——它只是菜单的输入,选中命令后不该留在输入框里。
 *
 * 识别规则与 onInput 完全一致(光标前的行尾 `/[a-z]*`),所以删除范围就是菜单
 * 正在消费的那段;不按"行首的 / 后面一串"来删,避免把 `/usr/bin/foo` 这类
 * 正文里的路径切掉开头。
 */
function stripSlashQuery() {
  const cursor = textareaEl.value?.selectionStart ?? value.value.length
  const before = value.value.slice(0, cursor)
  const matched = /\/([a-z]*)$/i.exec(before)
  if (!matched) return
  const start = cursor - matched[0].length
  value.value = value.value.slice(0, start) + value.value.slice(cursor)
  void nextTick(autoResize)
}

function onCmdSelect(cmd: CommandDef) {
  stripSlashQuery()
  cmd.run()
}

/** “+”菜单里点命令:先收起浮层再执行 —— 命令执行完菜单还挂在输入框上方会挡住输入区 */
function onAttachCommand(cmd: CommandDef) {
  attachMenuOpen.value = false
  cmd.run()
}

function onAttachMention(att: ChatAttachment) {
  attachments.value.push(att)
  // 移除输入框里的 @ 占位
  const idx = value.value.lastIndexOf('@')
  if (idx >= 0) value.value = value.value.slice(0, idx)
  mentionVisible.value = false
  requestAnimationFrame(autoResize)
}

async function onFileSelected(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (!files.length) return
  for (const f of files) {
    if (f.size > 20 * 1024 * 1024) {
      message.error(`「${f.name}」超过 20MB 限制`)
      continue
    }
    uploading.value = true
    try {
      const up = await uploadFile(f)
      const att: ChatAttachment = {
        url: up.url,
        filename: up.filename,
        kind: up.kind,
        content: up.kind === 'file' ? await readTextFile(f) : undefined,
      }
      attachments.value.push(att)
    } catch (err) {
      message.error(`上传失败:${(err as Error).message}`)
    } finally {
      uploading.value = false
    }
  }
}

function removeAttachment(index: number) {
  attachments.value.splice(index, 1)
}

async function submit() {
  const raw = value.value.trim()
  // 手敲的 /plan 前缀与令牌等价:发送前剥离,不进入消息正文
  const typed = PLAN_PREFIX_RE.exec(raw)
  const text = typed ? raw.slice(typed[0].length) : raw
  if (!text && !attachments.value.length) return
  if (showPlanToken.value && !text) {
    message.warning('请描述你的任务,例如:帮我重构这个模块')
    return
  }
  // 令牌即开关:挂了令牌开计划模式、没挂就关掉,避免上一次的计划模式残留
  const wantPlan = showPlanToken.value
  if (wantPlan !== convStore.planMode) {
    try {
      await convStore.setPlanMode(wantPlan)
    } catch (e) {
      message.error((e as Error).message)
      return
    }
  }
  const atts = [...attachments.value]
  emit('send', text, atts)
  // 令牌是"本条消息"的修饰,发出即消耗(会话仍处于计划模式时它会继续显示)
  planToken.value = false
  value.value = ''
  attachments.value = []
  cmdVisible.value = false
  mentionVisible.value = false
  requestAnimationFrame(autoResize)
}

function onStop() {
  emit('stop')
}

/** 粘贴图片/文档 → 自动上传并加入附件 */
async function onPaste(e: ClipboardEvent) {
  const items = Array.from(e.clipboardData?.items || [])
  const files: File[] = []
  for (const item of items) {
    if (item.kind !== 'file') continue
    const f = item.getAsFile()
    if (f) files.push(f)
  }
  if (!files.length) return
  e.preventDefault() // 阻止图片二进制贴进 textarea
  for (const f of files) {
    if (f.size > 20 * 1024 * 1024) {
      message.error(`「${f.name}」超过 20MB 限制`)
      continue
    }
    uploading.value = true
    try {
      const up = await uploadFile(f)
      const att: ChatAttachment = {
        url: up.url,
        filename: up.filename,
        kind: up.kind,
        content: up.kind === 'file' ? await readTextFile(f) : undefined,
      }
      attachments.value.push(att)
    } catch (err) {
      message.error(`上传失败:${(err as Error).message}`)
    } finally {
      uploading.value = false
    }
  }
}
</script>

<template>
  <div class="chat-input-wrap">
    <!-- 顶部行:仅新对话(无会话)时显示工作区选择 -->
    <div v-if="!convStore.currentId" class="chat-input-prebar">
      <WorkspaceSelect />
    </div>

    <div class="chat-input-box" :class="{ streaming }">
      <!-- 附件预览区 -->
      <div v-if="attachments.length" class="attachment-bar">
        <div v-for="(att, i) in attachments" :key="att.url + i" class="attachment-chip">
          <img
            v-if="att.kind === 'image'"
            class="attachment-thumb"
            :src="resolveAssetUrl(att.url)"
            alt=""
          />
          <svg v-else class="attachment-file-icon" viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
            <path d="M3 1.5a1 1 0 0 1 1-1h5l4 4v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-12zM9 1.5v3h3l-3-3z" />
          </svg>
          <span class="attachment-name" :title="att.filename">{{ att.filename }}</span>
          <button class="attachment-remove" title="移除" @click="removeAttachment(i)">
            <svg viewBox="0 0 12 12" width="10" height="10" fill="currentColor">
              <path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
            </svg>
          </button>
        </div>
      </div>

      <div class="input-popovers">
        <CommandMenu :show="cmdVisible" :query="cmdQuery" :commands="commands" @select="onCmdSelect" @close="cmdVisible = false" />
        <FileMention :show="mentionVisible" @attach="onAttachMention" @close="mentionVisible = false" />
      </div>

      <div class="input-row">
        <!-- 计划模式令牌:挂在输入框左侧,placeholder 仍然可见;点掉即取消本条消息的计划 -->
        <button
          v-if="showPlanToken"
          class="plan-token"
          title="本条消息走计划模式:先给计划,经你批准后再实施。点击取消"
          @click="removePlanToken"
        >
          <span class="plan-token-key">/plan</span>
          <svg class="plan-token-close" viewBox="0 0 12 12" width="9" height="9" fill="none">
            <path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
        </button>

        <textarea
          ref="textareaEl"
          v-model="value"
          class="msg-textarea"
          rows="1"
          :maxlength="MAX_LEN"
          :placeholder="placeholder"
          @input="onInput"
          @keydown="onKeydown"
          @paste="onPaste"
          @compositionstart="composing = true"
          @compositionend="composing = false"
        ></textarea>
      </div>

      <div class="chat-input-toolbar">
        <div class="chat-input-left">
          <!-- “+”按钮(附件/命令) -->
          <n-popover
            v-model:show="attachMenuOpen"
            trigger="click"
            placement="top-start"
            :show-arrow="false"
            :offset="6"
          >
            <template #trigger>
              <button class="attach-btn" title="添加附件" :disabled="uploading">
                <svg v-if="uploading" viewBox="0 0 16 16" width="16" height="16" fill="currentColor" class="attach-spin">
                  <path d="M8 2a6 6 0 1 0 6 6h-1.5A4.5 4.5 0 1 1 8 3.5V2z" />
                </svg>
                <svg v-else viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
                  <path d="M8 2a.5.5 0 0 1 .5.5v5h5a.5.5 0 0 1 0 1h-5v5a.5.5 0 0 1-1 0v-5h-5a.5.5 0 0 1 0-1h5v-5A.5.5 0 0 1 8 2z" />
                </svg>
              </button>
            </template>
            <div class="attach-menu">
              <div class="attach-menu-title">命令</div>
              <button v-for="cmd in commands" :key="cmd.key" class="attach-option" @click="onAttachCommand(cmd)">
                <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
                  <path d="M3 2h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zm1 2v8h8V4H4zm1 1h6v1H5V5zm0 2h4v1H5V7zm0 2h6v1H5V9z" />
                </svg>
                <span class="attach-opt-label">
                  <span class="attach-opt-key">/{{ cmd.key }}</span>
                  <span class="attach-opt-desc">{{ cmd.description }}</span>
                </span>
              </button>
            </div>
          </n-popover>

          <PermissionSelect />
          <ModelSelect />
        </div>
        <div class="chat-input-right">
          <span v-if="value.length > MAX_LEN - 500" class="char-count">{{ value.length }}/{{ MAX_LEN }}</span>
          <ContextRing :used="convStore.contextUsage.used" :window="contextWindow" />
          <button v-if="streaming" class="send-btn stop" title="停止生成" @click="onStop">
            <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
              <rect x="3.5" y="3.5" width="9" height="9" rx="1.5" />
            </svg>
          </button>
          <button v-else class="send-btn" :disabled="!value.trim() && !attachments.length" title="发送" @click="submit">
            <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
              <path d="M9 2.5a.5.5 0 0 1 .5-.5h4a.5.5 0 0 1 .5.5v4a.5.5 0 0 1-1 0V3.7L8.35 8.85a.5.5 0 0 1-.7-.7L12.29 3.5H9.5A.5.5 0 0 1 9 3z" />
              <path d="M2.5 2.7l11 5.3-11 5.3a.5.5 0 0 1-.72-.55l.8-3.2 5.4-1.55L2.58 6.45l-.8-3.2a.5.5 0 0 1 .72-.55z" />
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- 隐藏文件选择器 -->
    <input ref="fileInputEl" type="file" hidden multiple @change="onFileSelected" />
  </div>
</template>

<style scoped>
.chat-input-wrap {
  padding: 4px 20px 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
}
/* 顶部行:工作区(输入框上方一行,左对齐,宽度与输入框一致) */
.chat-input-prebar {
  width: 100%;
  max-width: 760px;
  display: flex;
  align-items: center;
  margin-bottom: 6px;
  padding-left: 2px;
}
.chat-input-box {
  position: relative;
  width: 100%;
  max-width: 760px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-strong);
  border-radius: 16px;
  padding: 12px 14px 8px;
  box-shadow: var(--shadow);
  transition: border-color 0.15s, box-shadow 0.15s;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.chat-input-box:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow);
}
.chat-input-box.streaming {
  border-color: var(--accent);
}
.input-popovers {
  position: relative;
  height: 0;
}
/* 令牌与输入框同一行:令牌在左、placeholder 紧随其后(前缀写进 textarea 会让 placeholder 消失) */
.input-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.plan-token {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  margin-top: 1px; /* 与 textarea 首行(行高 24px)对齐 */
  padding: 0 6px;
  border: none;
  border-radius: 6px;
  background: var(--accent-soft);
  color: var(--accent);
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s;
}
.plan-token:hover {
  background: var(--bg-active);
}
.plan-token-close {
  flex-shrink: 0;
  opacity: 0.75;
}
.input-row .msg-textarea {
  flex: 1 1 auto;
  width: auto;
  min-width: 0;
}
.msg-textarea {
  width: 100%;
  min-height: 24px;
  max-height: 192px;
  border: none;
  outline: none;
  background: transparent;
  resize: none;
  padding: 0;
  margin: 0;
  font-family: inherit;
  font-size: 15px;
  line-height: 24px;
  color: var(--text);
  overflow-y: auto;
}
.msg-textarea::placeholder {
  color: var(--text-tertiary);
}
.attachment-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.attachment-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--bg-hover);
  border-radius: 8px;
  padding: 4px 6px;
  font-size: 12px;
  color: var(--text);
  max-width: 220px;
}
.attachment-thumb {
  width: 26px;
  height: 26px;
  border-radius: 4px;
  object-fit: cover;
  flex-shrink: 0;
}
.attachment-file-icon {
  color: var(--text-secondary);
  flex-shrink: 0;
}
.attachment-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  max-width: 150px;
}
.attachment-remove {
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
}
.attachment-remove:hover {
  color: var(--danger);
  background: var(--danger-soft);
}
.chat-input-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.chat-input-left {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.chat-input-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.char-count {
  font-size: 11px;
  color: var(--text-tertiary);
}
.attach-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.attach-btn:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--accent);
}
.attach-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.attach-spin {
  animation: attach-rotate 1s linear infinite;
}
@keyframes attach-rotate {
  to {
    transform: rotate(360deg);
  }
}
.attach-menu {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 230px;
  padding: 2px;
}
.attach-menu-title {
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 6px 10px 2px;
  letter-spacing: 0.5px;
}
.attach-option {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  color: var(--text);
  padding: 8px 10px;
  border-radius: 7px;
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  transition: background 0.12s;
  text-align: left;
}
.attach-option > svg {
  flex-shrink: 0;
  color: var(--text-secondary);
}
.attach-option:hover {
  background: var(--bg-hover);
}
.attach-option:hover > svg {
  color: var(--accent);
}
.attach-opt-label {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.attach-opt-key {
  font-weight: 600;
  color: var(--text);
  font-size: 13px;
}
.attach-opt-desc {
  font-size: 11px;
  color: var(--text-tertiary);
}
.send-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.15s, opacity 0.15s, transform 0.1s;
}
.send-btn:hover:not(:disabled) {
  background: var(--accent-hover);
  transform: scale(1.05);
}
.send-btn:active:not(:disabled) {
  transform: scale(0.95);
}
.send-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.send-btn.stop {
  background: var(--text-secondary);
}
.send-btn.stop:hover {
  background: var(--danger);
}
</style>
