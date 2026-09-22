import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import { resolveAssetUrl } from './asset'

// markdown-it 单例(html:false 防 XSS)
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

md.options.highlight = (str: string, lang: string): string => {
  if (lang && hljs.getLanguage(lang)) {
    try {
      return `<pre class="hljs"><code>${hljs.highlight(str, { language: lang, ignoreIllegals: true }).value}</code></pre>`
    } catch {
      // fallthrough
    }
  }
  try {
    const auto = hljs.highlightAuto(str)
    if (auto.relevance > 0) {
      return `<pre class="hljs"><code>${auto.value}</code></pre>`
    }
  } catch {
    // fallthrough
  }
  return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`
}

// 覆写 image 渲染器:相对路径补全(经反代/直连后端)
const defaultImageRender = md.renderer.rules.image as NonNullable<typeof md.renderer.rules.image>

md.renderer.rules.image = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const src = token.attrGet('src')
  if (src) {
    token.attrSet('src', resolveAssetUrl(src))
  }
  return defaultImageRender(tokens, idx, options, env, self)
}

export function renderMarkdown(content: string): string {
  if (!content) return ''
  return md.render(content)
}
