import MarkdownIt from 'markdown-it'
import { COPY_LABEL } from './clipboard'
import { highlightCode } from './highlight'
import { resolveAssetUrl } from './asset'

// markdown-it 单例(html:false 防 XSS)
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

/**
 * 代码块 HTML:高亮 + 一键复制按钮。
 *
 * 覆写 fence / code_block 两个规则(而不是只给 `options.highlight` 传函数),
 * 因为要额外套一层 `.code-wrap` 把按钮定位到右上角 —— 按钮必须在 `<pre>` **外面**,
 * 否则会被 `overflow-x: auto` 裁掉。点击由根节点的事件委托处理(见 composables/useCodeCopy)。
 */
function renderCodeBlock(code: string, lang: string): string {
  return (
    '<div class="code-wrap">' +
    `<pre class="hljs"><code>${highlightCode(code, lang)}</code></pre>` +
    `<button type="button" class="code-copy-btn">${COPY_LABEL}</button>` +
    '</div>\n'
  )
}

// 围栏代码块(```lang)
md.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  const lang = (token.info || '').trim().split(/\s+/)[0] || ''
  return renderCodeBlock(token.content, lang)
}

// 缩进式代码块(无语言标注)
md.renderer.rules.code_block = (tokens, idx) => renderCodeBlock(tokens[idx].content, '')

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
