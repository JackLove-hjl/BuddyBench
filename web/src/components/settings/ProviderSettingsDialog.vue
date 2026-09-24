<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  NButton,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NPopconfirm,
  NSelect,
  NSpin,
  NTabs,
  NTabPane,
  useMessage,
} from 'naive-ui'
import { PROVIDER_PRESETS } from '../../config/providerPresets'
import { useProviderStore } from '../../stores/provider'
import { useModelStore } from '../../stores/model'
import type { ProviderInfo, ProviderModelItem } from '../../api/providers'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'update:show', v: boolean): void }>()

const message = useMessage()
const providerStore = useProviderStore()
const modelStore = useModelStore()

const activeTab = ref('list')
const saving = ref(false)
const discovering = ref(false)
const editingId = ref<string | null>(null)

/** 内置模型(来自后端 .env 的 BUILTIN_MODELS):设置页只读展示 */
const builtinModels = computed(() => modelStore.models.filter((m) => m.provider === '内置模型'))

interface ModelRow {
  id: string
  input_tokens: string
  output_tokens: string
  /** 思考强度:null / 空 = 不下发 reasoning_effort(用端点默认行为) */
  reasoning_effort: string | null
}

/** 思考强度预设(filterable + tag,允许填端点特有的取值) */
const REASONING_OPTIONS = [
  { label: 'low', value: 'low' },
  { label: 'medium', value: 'medium' },
  { label: 'high', value: 'high' },
]

const form = reactive({
  name: '',
  provider_type: 'deepseek',
  base_url: '',
  api_key: '',
  // 手动录入的模型:名称 + 输入/输出上下文(不再从 /models 自动同步)
  models: [] as ModelRow[],
})

const toRows = (items: ProviderModelItem[]): ModelRow[] =>
  items.map((m) => ({
    id: m.id,
    input_tokens: m.input_tokens ? String(m.input_tokens) : '',
    output_tokens: m.output_tokens ? String(m.output_tokens) : '',
    reasoning_effort: m.reasoning_effort || null,
  }))

function addModelRow() {
  form.models.push({ id: '', input_tokens: '', output_tokens: '', reasoning_effort: null })
}

function removeModelRow(index: number) {
  form.models.splice(index, 1)
}

/** 把表格转为请求体:丢弃空行,数字串解析为数字 */
function collectModels(): ProviderModelItem[] {
  return form.models
    .map((row) => ({
      id: row.id.trim(),
      input_tokens: Number(row.input_tokens) > 0 ? Number(row.input_tokens) : undefined,
      output_tokens: Number(row.output_tokens) > 0 ? Number(row.output_tokens) : undefined,
      reasoning_effort: row.reasoning_effort?.trim() || undefined,
    }))
    .filter((m) => m.id)
}

/** 探测端点上的候选模型名并补进表格(仅"发现",上下文仍需用户填写) */
async function onDiscover() {
  if (!editingId.value) return
  discovering.value = true
  try {
    const found = await providerStore.discover(editingId.value)
    const existing = new Set(form.models.map((m) => m.id.trim()).filter(Boolean))
    let added = 0
    for (const item of found) {
      if (existing.has(item.id)) continue
      existing.add(item.id)
      form.models.push({ id: item.id, input_tokens: '', output_tokens: '', reasoning_effort: null })
      added += 1
    }
    message.success(
      added ? `发现 ${found.length} 个模型,新增 ${added} 行(请补全输入/输出上下文)` : `发现 ${found.length} 个模型,列表中已存在`,
    )
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    discovering.value = false
  }
}

const isEdit = computed(() => editingId.value !== null)

const typeOptions = PROVIDER_PRESETS.map((p) => ({ label: p.label, value: p.key }))

const typeLabel = (key: string) => PROVIDER_PRESETS.find((p) => p.key === key)?.label || key

function onTypeChange() {
  // 切换类型时:仅当是"新建"或用户未改动过 base_url 时填充默认值
  const preset = PROVIDER_PRESETS.find((p) => p.key === form.provider_type)
  if (preset && preset.default_base_url) {
    form.base_url = preset.default_base_url
  }
}

function resetForm() {
  editingId.value = null
  form.name = ''
  form.provider_type = 'deepseek'
  form.base_url = 'https://api.deepseek.com'
  form.api_key = ''
  form.models = []
  activeTab.value = 'list'
}

function openCreate() {
  resetForm()
  if (!form.models.length) addModelRow()
  activeTab.value = 'form'
}

function openEdit(p: ProviderInfo) {
  editingId.value = p.id
  form.name = p.name
  form.provider_type = p.provider_type
  form.base_url = p.base_url
  form.api_key = ''
  form.models = toRows(p.models || [])
  if (!form.models.length) addModelRow()
  activeTab.value = 'form'
}

async function onSave() {
  if (!form.name.trim()) {
    message.warning('请输入实例名称')
    return
  }
  if (!form.base_url.trim()) {
    message.warning('请输入 base_url')
    return
  }
  if (!form.api_key.trim()) {
    message.warning('请输入 API Key')
    return
  }
  saving.value = true
  const models = collectModels()
  if (!models.length) {
    saving.value = false
    message.warning('请至少录入一个模型(名称 + 上下文)')
    return
  }
  try {
    if (isEdit.value) {
      await providerStore.edit(editingId.value!, {
        name: form.name.trim(),
        provider_type: form.provider_type,
        base_url: form.base_url.trim(),
        api_key: form.api_key.trim(),
        models,
      })
      message.success('供应商已更新')
    } else {
      await providerStore.add({
        name: form.name.trim(),
        provider_type: form.provider_type,
        base_url: form.base_url.trim(),
        api_key: form.api_key.trim(),
        models,
      })
      message.success(`供应商已添加,共 ${models.length} 个模型`)
    }
    resetForm()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

async function onRemove(p: ProviderInfo) {
  try {
    await providerStore.remove(p.id)
    message.success(`已删除 ${p.name}`)
  } catch (e) {
    message.error((e as Error).message)
  }
}

onMounted(() => {
  void providerStore.fetchProviders()
  void modelStore.fetchModels() // 内置模型只读展示用
})
</script>

<template>
  <n-modal
    :show="props.show"
    preset="card"
    class="settings-modal"
    title="设置"
    style="width: 720px"
    :bordered="false"
    :mask-closable="true"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <div class="settings-body">
      <n-tabs v-model:value="activeTab" type="line" animated>
        <!-- 供应商列表 -->
        <n-tab-pane name="list" tab="模型供应商">
          <!-- 内置模型:来自后端 .env 的 BUILTIN_MODELS,只读展示 -->
          <div v-if="builtinModels.length" class="builtin-block">
            <div class="builtin-head">
              <span class="builtin-title">内置模型</span>
              <span class="builtin-badge">来自 .env · 只读</span>
            </div>
            <div class="builtin-row" v-for="m in builtinModels" :key="m.id">
              <span class="builtin-id">{{ m.display_name }}</span>
              <span class="builtin-ctx">
                输入 {{ m.input_tokens ? m.input_tokens.toLocaleString() : '未配置' }} · 输出
                {{ m.output_tokens ? m.output_tokens.toLocaleString() : '未配置' }}
              </span>
            </div>
            </div>

          <div class="provider-toolbar">
            <span class="provider-toolbar-hint">已添加 {{ providerStore.providers.length }} 个供应商</span>
            <n-button size="small" type="primary" @click="openCreate">
              <template #icon>
                <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
                  <path d="M8 2a.5.5 0 0 1 .5.5v5h5a.5.5 0 0 1 0 1h-5v5a.5.5 0 0 1-1 0v-5h-5a.5.5 0 0 1 0-1h5v-5A.5.5 0 0 1 8 2z" />
                </svg>
              </template>
              添加供应商
            </n-button>
          </div>

          <n-spin :show="providerStore.loading">
            <div v-if="!providerStore.providers.length" class="provider-empty">
              <n-empty description="还没有配置供应商,点击「添加供应商」开始">
                <template #extra>
                  <span class="provider-empty-hint">
                    支持 OpenAI / Anthropic / DeepSeek 等 OpenAI 兼容平台,保存后自动查询模型
                  </span>
                </template>
              </n-empty>
            </div>

            <div v-else class="provider-list">
              <div v-for="p in providerStore.providers" :key="p.id" class="provider-card">
                <div class="provider-card-main">
                  <div class="provider-card-head">
                    <span class="provider-name">{{ p.name }}</span>
                    <span class="provider-badge">{{ typeLabel(p.provider_type) }}</span>
                  </div>
                  <div class="provider-url">{{ p.base_url }}</div>
                  <div class="provider-meta">
                    <span class="provider-key-masked">{{ p.api_key_masked || '未填写 Key' }}</span>
                    <span class="provider-models">共 {{ p.models.length }} 个模型</span>
                  </div>
                  <div v-if="p.models.length" class="provider-model-chips">
                    <span v-for="m in p.models.slice(0, 6)" :key="m.id" class="model-chip">{{ m.id }}</span>
                    <span v-if="p.models.length > 6" class="model-chip more">+{{ p.models.length - 6 }}</span>
                  </div>
                </div>
                <div class="provider-card-actions">
                  <n-button size="tiny" quaternary @click="openEdit(p)">编辑</n-button>
                  <n-popconfirm @positive-click="onRemove(p)" positive-text="删除" negative-text="取消">
                    <template #trigger>
                      <n-button size="tiny" quaternary type="error">删除</n-button>
                    </template>
                    删除后该供应商下所有模型将不可用,确定?
                  </n-popconfirm>
                </div>
              </div>
            </div>
          </n-spin>
        </n-tab-pane>

        <!-- 添加 / 编辑表单 -->
        <n-tab-pane name="form" :tab="isEdit ? '编辑供应商' : '添加供应商'">
          <n-form label-placement="top" size="small">
            <n-form-item label="供应商类型">
              <n-select
                v-model:value="form.provider_type"
                :options="typeOptions"
                @update:value="onTypeChange"
              />
            </n-form-item>
            <n-form-item label="实例名称">
              <n-input
                v-model:value="form.name"
                placeholder="例如:我的 DeepSeek"
                autocomplete="off"
                :input-props="{ name: 'provider-name', autocomplete: 'off' }"
              />
            </n-form-item>
            <n-form-item label="Base URL(OpenAI 兼容端点)">
              <n-input
                v-model:value="form.base_url"
                placeholder="https://api.deepseek.com"
                autocomplete="off"
                :input-props="{ name: 'provider-base-url', autocomplete: 'off' }"
              />
            </n-form-item>
            <n-form-item label="API Key">
              <n-input
                v-model:value="form.api_key"
                type="password"
                show-password-on="click"
                :placeholder="isEdit ? '留空则保持原 Key 不变' : 'sk-...'"
                autocomplete="new-password"
                :input-props="{ name: 'provider-api-key', autocomplete: 'new-password' }"
              />
            </n-form-item>
            <n-form-item label="模型列表(手动录入)">
              <div class="model-editor">
                <div class="model-editor-head">
                  <span class="model-col-name">模型名</span>
                  <span class="model-col-num">输入上下文</span>
                  <span class="model-col-num">最大输出</span>
                  <span class="model-col-num">思考强度</span>
                  <span class="model-col-op"></span>
                </div>
                <div v-for="(row, i) in form.models" :key="i" class="model-editor-row">
                  <n-input v-model:value="row.id" size="small" placeholder="deepseek-v4.1-flash" />
                  <n-input v-model:value="row.input_tokens" size="small" placeholder="1000000" />
                  <n-input v-model:value="row.output_tokens" size="small" placeholder="384000" />
                  <n-select
                    v-model:value="row.reasoning_effort"
                    size="small"
                    clearable
                    filterable
                    tag
                    placeholder="默认"
                    :options="REASONING_OPTIONS"
                  />
                  <n-button size="tiny" quaternary type="error" @click="removeModelRow(i)">删除</n-button>
                </div>
                <div class="model-editor-actions">
                  <n-button size="tiny" quaternary @click="addModelRow">+ 添加模型</n-button>
                  <n-button v-if="isEdit" size="tiny" quaternary :loading="discovering" @click="onDiscover">
                    从端点发现模型名
                  </n-button>
                </div>
              </div>
            </n-form-item>
            <div class="form-actions">
              <n-button @click="activeTab = 'list'">取消</n-button>
              <n-button type="primary" :loading="saving" @click="onSave">
                {{ isEdit ? '保存' : '添加' }}
              </n-button>
            </div>
          </n-form>
        </n-tab-pane>
      </n-tabs>
    </div>
  </n-modal>
</template>

<style scoped>
.settings-body {
  min-height: 320px;
}
.provider-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.provider-toolbar-hint {
  font-size: 12px;
  color: var(--text-tertiary);
}
.provider-empty {
  padding: 40px 0;
}
.provider-empty-hint {
  font-size: 12px;
  color: var(--text-tertiary);
}
.provider-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 360px;
  overflow-y: auto;
}
.provider-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  background: var(--bg-elevated);
}
.provider-card-main {
  flex: 1;
  min-width: 0;
}
.provider-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.provider-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}
.provider-badge {
  font-size: 11px;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 4px;
  padding: 1px 6px;
}
.provider-url {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 3px;
  word-break: break-all;
}
.provider-meta {
  display: flex;
  gap: 12px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-tertiary);
}
.provider-key-masked {
  font-family: monospace;
}
.provider-model-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 8px;
}
.model-chip {
  font-size: 11px;
  background: var(--bg-code);
  color: var(--text-secondary);
  border-radius: 4px;
  padding: 1px 6px;
}
.model-chip.more {
  color: var(--text-tertiary);
}
.provider-card-actions {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex-shrink: 0;
}
/* 内置模型只读展示 */
.builtin-block {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 12px;
  background: var(--bg-elevated);
}
.builtin-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.builtin-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.builtin-badge {
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-hover);
  border-radius: 4px;
  padding: 1px 6px;
}
.builtin-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  padding: 3px 0;
}
.builtin-id {
  font-family: 'SFMono-Regular', Consolas, monospace;
  color: var(--text);
}
.builtin-ctx {
  color: var(--text-tertiary);
}
/* 模型录入表格 */
.model-editor {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.model-editor-head,
.model-editor-row {
  display: grid;
  grid-template-columns: 1.3fr 1fr 1fr 0.9fr auto;
  gap: 6px;
  align-items: center;
}
.model-editor-head {
  font-size: 11px;
  color: var(--text-tertiary);
}
.model-editor-actions {
  display: flex;
  gap: 8px;
}
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
</style>
