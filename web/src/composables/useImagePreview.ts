import { readonly, ref } from 'vue'

/**
 * 全站共用的图片放大预览。
 *
 * 任意组件调用 openImage(url) 即可,由 App.vue 里挂载的 ImagePreview 渲染浮层 ——
 * 只维护一份全局状态,避免工具卡、用户附件等每个地方各写一套放大逻辑。
 */
export interface PreviewImage {
  url: string
  /** 浮层角落的小标注(如文件名 / "屏幕截图"),可空 */
  label: string
}

const current = ref<PreviewImage | null>(null)

export function useImagePreview() {
  return {
    /** 只读:渲染方读取当前预览对象 */
    current: readonly(current),
    openImage(url: string, label = '') {
      if (!url) return
      current.value = { url, label }
    },
    closeImage() {
      current.value = null
    },
  }
}
