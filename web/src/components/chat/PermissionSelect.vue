<script setup lang="ts">
import { computed, ref } from 'vue'
import { NPopconfirm, NPopover, useMessage } from 'naive-ui'
import { useConversationStore } from '../../stores/conversation'
import { PERMISSION_FULL_ACCESS, PERMISSION_LABELS, type Permission } from '../../types'

const convStore = useConversationStore()
const message = useMessage()
const pendingConfirm = ref(false)
const popoverOpen = ref(false)

/** 参考 DeepSeek Harness design set 1556 的权限图标:
 *  read-only = 盾牌+对勾; workspace-write = 盾牌+铅笔; full-access = 盾牌+感叹号 */
const shieldOutline =
  'M8.20554 0.899994L14.7901 3.36857V7.01026C14.7901 12 11.0466 14.2103 8.20554 15.3C5.36446 14.2103 1.62012 12 1.62012 7.01026V3.36857L8.20554 0.899994Z'

interface GlyphPath {
  d: string
  stroke: boolean
  fill: boolean
}

function checkGlyph(): GlyphPath[] {
  return [
    { d: shieldOutline, stroke: true, fill: false },
    {
      d: 'M12.1654 5.7552L8.9447 9.41475C8.73044 9.65816 8.53628 9.8804 8.35774 10.0423C8.1713 10.2114 7.94235 10.3717 7.64016 10.4254C7.48207 10.4535 7.32 10.4552 7.16151 10.4294C6.85843 10.3801 6.62728 10.2223 6.43836 10.0559C6.25752 9.89653 6.06037 9.67732 5.84264 9.43705L4.72925 8.20897L5.63557 7.38707L6.74897 8.61594C6.98603 8.87755 7.12974 9.03533 7.24673 9.13839C7.31033 9.19443 7.34485 9.21476 7.35823 9.22122C7.38068 9.22484 7.40352 9.22515 7.42593 9.22122C7.40522 9.22502 7.42893 9.23294 7.53583 9.136C7.65132 9.03126 7.79316 8.87139 8.02643 8.60638L11.2479 4.94763L12.1654 5.7552Z',
      stroke: false,
      fill: true,
    },
  ]
}

function pencilGlyph(): GlyphPath[] {
  return [
    {
      d: 'M8.08887 0.251709C8.20479 0.23085 8.32486 0.241168 8.43652 0.282959L15.0215 2.75171C15.2787 2.84819 15.4492 3.09414 15.4492 3.3689V7.0105C15.4492 7.10986 15.4441 7.2081 15.4414 7.30542C15.0285 7.07175 14.5905 6.87695 14.1309 6.73022V3.82495L8.20508 1.60327L2.2793 3.82495V7.0105C2.27936 9.7171 3.4745 11.5379 5.02734 12.7947C5.01025 12.9942 5 13.1962 5 13.4001C5.00001 13.7617 5.02722 14.1169 5.08008 14.4636C2.91555 13.0393 0.961014 10.752 0.960938 7.0105V3.3689C0.960938 3.09417 1.13146 2.84821 1.38867 2.75171L7.97461 0.282959L8.08887 0.251709Z',
      stroke: false,
      fill: true,
    },
    { d: 'M11.3525 5.64688V6.85688H5V5.64688H11.3525Z', stroke: false, fill: true },
    { d: 'M9.5824 8.29376V9.50376H5V8.29376H9.5824Z', stroke: false, fill: true },
    { d: 'M14.6647 15.6852H10.0338C10.3878 15.3751 10.7567 15.0517 11.0772 14.7706C11.2531 14.6164 11.4144 14.4746 11.5511 14.3547H14.6647V15.6852Z', stroke: false, fill: true },
    {
      d: 'M8.14852 14.1308L7.33925 15.4976C7.22458 15.6912 7.42245 15.9194 7.63037 15.8333L9.09785 15.2254L15.0399 10.0719L14.0905 8.97733L8.14852 14.1308Z',
      stroke: false,
      fill: true,
    },
  ]
}

function exclamationGlyph(): GlyphPath[] {
  return [
    { d: shieldOutline, stroke: true, fill: false },
    { d: 'M9.10094 4.5V8.75939H7.59888V4.5H9.10094Z', stroke: false, fill: true },
    { d: 'M9.10094 9.8114V11.5H7.59888V9.8114H9.10094Z', stroke: false, fill: true },
  ]
}

interface PermOption {
  value: Permission
  label: string
  description: string
  paths: GlyphPath[]
}

const options: PermOption[] = [
  { value: 'read_only', label: PERMISSION_LABELS.read_only, description: '仅读文件,禁写禁命令', paths: checkGlyph() },
  { value: 'workspace_writable', label: PERMISSION_LABELS.workspace_writable, description: '工作区内读写+命令', paths: pencilGlyph() },
  { value: 'full_access', label: PERMISSION_LABELS.full_access, description: '不受限读写与命令', paths: exclamationGlyph() },
]

const current = computed(() => convStore.permission)
const currentOption = computed(() => options.find((o) => o.value === current.value))

async function choose(p: Permission) {
  popoverOpen.value = false
  if (p === PERMISSION_FULL_ACCESS) {
    pendingConfirm.value = true
    return
  }
  await doSet(p)
}

async function doSet(p: Permission) {
  try {
    await convStore.setPermission(p)
    message.success(`权限已切换为「${PERMISSION_LABELS[p]}」`)
  } catch (e) {
    message.error((e as Error).message)
  }
}
</script>

<template>
  <div class="permission-select">
    <n-popover
      v-model:show="popoverOpen"
      trigger="click"
      placement="top-start"
      :show-arrow="false"
      :offset="6"
      :body-style="{ padding: '6px', borderRadius: '10px' }"
    >
      <template #trigger>
        <button class="perm-trigger" :title="currentOption?.description">
          <span class="perm-glyph">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
              <path
                v-for="(p, i) in (currentOption?.paths || [])"
                :key="i"
                :d="p.d"
                :stroke="p.stroke ? 'currentColor' : 'none'"
                :fill="p.fill ? 'currentColor' : 'none'"
                stroke-width="1.31831"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          <span class="perm-trigger-label">{{ currentOption?.label || PERMISSION_LABELS[current] }}</span>
          <svg class="perm-chevron" viewBox="0 0 12 12" width="10" height="10" fill="currentColor">
            <path d="M2 4l4 4 4-4" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </template>

      <div class="perm-menu">
        <button
          v-for="o in options"
          :key="o.value"
          class="perm-option"
          :class="{ active: o.value === current }"
          @click="choose(o.value)"
        >
          <span class="perm-glyph">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
              <path
                v-for="(p, i) in o.paths"
                :key="i"
                :d="p.d"
                :stroke="p.stroke ? 'currentColor' : 'none'"
                :fill="p.fill ? 'currentColor' : 'none'"
                stroke-width="1.31831"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          <span class="perm-option-text">
            <span class="perm-option-label">{{ o.label }}</span>
            <span class="perm-option-desc">{{ o.description }}</span>
          </span>
          <svg v-if="o.value === current" class="perm-check" viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
            <path d="M6.5 11.5l-3.5-3.5 1-1 2.5 2.5 5-5 1 1-6 6z" />
          </svg>
        </button>
      </div>
    </n-popover>

    <n-popconfirm
      :show="pendingConfirm"
      positive-text="确认开启"
      negative-text="取消"
      @positive-click="doSet('full_access'); pendingConfirm = false"
      @update:show="(v: boolean) => (pendingConfirm = v)"
    >
      <template #trigger>
        <span style="display: none"></span>
      </template>
      全部权限允许 Agent 不受限地读写任意文件并执行命令,存在安全风险,确认开启?
    </n-popconfirm>
  </div>
</template>

<style scoped>
.permission-select {
  display: flex;
  align-items: center;
}
.perm-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  border: none;
  background: var(--bg-hover);
  color: var(--text);
  border-radius: 8px;
  padding: 5px 10px;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  transition: background 0.15s;
  white-space: nowrap;
}
.perm-trigger:hover {
  background: var(--bg-active);
}
.perm-glyph {
  display: inline-flex;
  align-items: center;
  color: currentColor;
  flex-shrink: 0;
}
.perm-trigger-label {
  line-height: 1;
}
.perm-chevron {
  color: var(--text-secondary);
  flex-shrink: 0;
}
.perm-menu {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 220px;
}
.perm-option {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  border: none;
  background: transparent;
  color: var(--text);
  padding: 8px 10px;
  border-radius: 7px;
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  transition: background 0.12s;
}
.perm-option:hover {
  background: var(--bg-hover);
}
.perm-option.active {
  background: var(--accent-soft);
  color: var(--accent);
}
.perm-option-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.perm-option-label {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.3;
}
.perm-option-desc {
  font-size: 11px;
  color: var(--text-tertiary);
  line-height: 1.3;
}
.perm-check {
  flex-shrink: 0;
}
</style>
