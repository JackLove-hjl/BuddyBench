/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

/**
 * highlight.js 按需导入的语言模块。
 *
 * 包里的 exports 映射(`"./lib/languages/*"`)没有声明 types,而语言文件旁边也没有
 * 对应的 .d.ts,TS 会报「找不到声明文件」。运行时这些模块就是 export default 一个
 * LanguageFn,所以这里按实际形状补一个声明(与官方 types/index.d.ts 的 LanguageFn 一致)。
 */
declare module 'highlight.js/lib/languages/*' {
  import type { LanguageFn } from 'highlight.js'
  const language: LanguageFn
  export default language
}
