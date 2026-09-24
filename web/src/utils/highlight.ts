/**
 * 代码高亮统一入口(highlight.js)。
 *
 * Markdown 代码块与工具卡(正在生成的代码 / diff / 命令 / 参数)共用这里的实现,
 * token 配色由 styles/markdown.css 的 `.hljs-*` 规则提供(亮/暗双主题)。
 *
 * 所有返回值都是**已转义的 HTML**,可直接交给 v-html。
 */
import hljs from 'highlight.js'

/** 扩展名 → hljs 语言名(工具卡按文件路径推断语言) */
const LANG_BY_EXT: Record<string, string> = {
  py: 'python',
  ts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  jsx: 'jsx',
  mjs: 'javascript',
  cjs: 'javascript',
  vue: 'html',
  html: 'html',
  css: 'css',
  scss: 'scss',
  less: 'less',
  json: 'json',
  md: 'markdown',
  yml: 'yaml',
  yaml: 'yaml',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  ps1: 'powershell',
  sql: 'sql',
  rs: 'rust',
  go: 'go',
  java: 'java',
  kt: 'kotlin',
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  cs: 'csharp',
  php: 'php',
  rb: 'ruby',
  toml: 'ini',
  ini: 'ini',
  conf: 'ini',
  xml: 'xml',
  dockerfile: 'dockerfile',
}

/** 由文件路径推断 hljs 语言名;无法识别时返回空串(交给调用方决定是否自动探测) */
export function langFromPath(path: string): string {
  if (!path) return ''
  const name = path.split(/[\\/]/).pop() || ''
  // Dockerfile / Makefile 这类无扩展名的文件按文件名判断
  const lower = name.toLowerCase()
  if (lower === 'dockerfile') return 'dockerfile'
  if (lower === 'makefile') return 'makefile'
  const ext = lower.includes('.') ? lower.split('.').pop()! : ''
  return LANG_BY_EXT[ext] || ''
}

/** 自动探测只对中等长度的文本做:highlightAuto 要遍历全部语言,长文本下开销明显 */
const AUTO_DETECT_LIMIT = 20000

const ESCAPE_MAP: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (ch) => ESCAPE_MAP[ch])
}

/**
 * 高亮一段代码,返回已转义的 HTML。
 *
 * @param lang        hljs 语言名(空串 / `text` 视为未指定)
 * @param autoDetect  未指定语言时是否自动探测。纯文本输出(日志、控制台)可关掉,避免被误上色
 */
export function highlightCode(code: string, lang = '', autoDetect = true): string {
  const text = code || ''
  if (!text) return ''

  const language = lang.trim()
  if (language && language !== 'text' && hljs.getLanguage(language)) {
    try {
      return hljs.highlight(text, { language, ignoreIllegals: true }).value
    } catch {
      // 落到自动探测
    }
  }

  if (autoDetect && text.length <= AUTO_DETECT_LIMIT) {
    try {
      const auto = hljs.highlightAuto(text)
      // relevance 为 0 = 没认出任何语言,原样输出,避免纯文本被逐词乱上色
      if (auto.relevance > 0) return auto.value
    } catch {
      // 落到转义原文
    }
  }

  return escapeHtml(text)
}
