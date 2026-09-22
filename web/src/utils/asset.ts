/** 图片相对路径解析:同源原样返回 / 增加 VITE_API_BASE 前缀 */
export function resolveAssetUrl(src: string): string {
  if (!src) return src
  // LLM 可能把 /images/... 推断成绝对 URL(如 https://d.study.llm/images/x.png),
  // 只要路径指向本系统 /images 资源就归一化为相对路径,经 vite 代理加载
  if (/^https?:\/\//i.test(src)) {
    try {
      const u = new URL(src)
      if (u.pathname.startsWith('/images/')) return u.pathname
    } catch {
      // 解析失败按相对路径处理
    }
  }
  const base = import.meta.env.VITE_API_BASE as string | undefined
  if (!base) return src
  return `${base.replace(/\/+$/, '')}${src}`
}
