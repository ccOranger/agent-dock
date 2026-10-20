/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

// markdown-it 未随包提供类型声明（项目未装 @types/markdown-it）：
// 这里只声明实际用到的选项与方法，保证 vue-tsc 严格模式下可过
declare module 'markdown-it' {
  interface MarkdownItOptions {
    html?: boolean
    linkify?: boolean
    highlight?: (code: string, lang: string) => string
  }

  class MarkdownIt {
    constructor(options?: MarkdownItOptions)
    render(src: string): string
  }

  export default MarkdownIt
}
