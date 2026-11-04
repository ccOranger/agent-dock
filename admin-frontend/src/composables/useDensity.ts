import { ref } from 'vue'

/**
 * 表格密度：舒适 / 紧凑。
 *
 * 只切两个 CSS 变量（单元格上下留白与字号），由 theme.css 消费，
 * 因此对 Element Plus 的所有表格一次性生效，不需要各页改模板。
 * 选择持久化在 localStorage，并在 main.ts 里于挂载前应用，避免首屏闪一下再变。
 */
export type Density = 'comfortable' | 'compact'

const KEY = 'cs-admin:density'
const density = ref<Density>('comfortable')

function apply() {
  document.documentElement.dataset.density = density.value
}

/** 挂载前调用：读取上次选择并立即生效 */
export function initDensity() {
  density.value = localStorage.getItem(KEY) === 'compact' ? 'compact' : 'comfortable'
  apply()
}

export function useDensity() {
  function toggle() {
    density.value = density.value === 'compact' ? 'comfortable' : 'compact'
    localStorage.setItem(KEY, density.value)
    apply()
  }
  return { density, toggle }
}
