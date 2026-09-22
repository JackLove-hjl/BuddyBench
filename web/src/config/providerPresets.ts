/** 供应商类型预设:选择类型时自动填充默认 base_url(可修改) */

export interface ProviderPreset {
  key: string
  label: string
  default_base_url: string
  hint?: string
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    key: 'openai',
    label: 'OpenAI',
    default_base_url: 'https://api.openai.com/v1',
    hint: 'OpenAI 官方 API',
  },
  {
    key: 'anthropic',
    label: 'Anthropic',
    default_base_url: 'https://api.anthropic.com/v1',
    hint: 'Claude 系列(OpenAI 兼容端点)',
  },
  {
    key: 'deepseek',
    label: 'DeepSeek',
    default_base_url: 'https://api.deepseek.com',
    hint: 'DeepSeek 官方 API',
  },
  {
    key: 'qwen',
    label: '通义千问',
    default_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    hint: '阿里云 DashScope(OpenAI 兼容)',
  },
  {
    key: 'zhipu',
    label: '智谱 AI',
    default_base_url: 'https://open.bigmodel.cn/api/paas/v4',
    hint: 'GLM 系列',
  },
  {
    key: 'moonshot',
    label: 'Moonshot Kimi',
    default_base_url: 'https://api.moonshot.cn/v1',
    hint: 'Kimi 系列',
  },
  {
    key: 'siliconflow',
    label: '硅基流动',
    default_base_url: 'https://api.siliconflow.cn/v1',
    hint: 'SiliconFlow 聚合平台',
  },
  {
    key: 'custom',
    label: '自定义(OpenAI 兼容)',
    default_base_url: '',
    hint: '任意 OpenAI 兼容端点',
  },
]

export function getPreset(key: string): ProviderPreset | undefined {
  return PROVIDER_PRESETS.find((p) => p.key === key)
}
