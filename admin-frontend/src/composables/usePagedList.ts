import { ref, type Ref } from 'vue'

export interface PageResult<T> {
  items: T[]
  total: number
}

/** 分页取数函数：params 由页面按当前筛选条件拼好传入 */
export type PagedFetcher<T> = (
  params: Record<string, unknown>,
  page: number,
  pageSize: number,
) => Promise<PageResult<T>>

/**
 * 列表页通用分页状态：统一 loading / page / pageSize / total 与重载语义。
 *
 * 之前的写法每页各写一份 load()/reload()，页码与筛选重置容易写错；
 * 这里只收敛状态，筛选条件仍由页面自己持有（各页字段差异太大）。
 *
 * 用法：
 *   const list = usePagedList(apiListTickets)
 *   await list.load(buildParams())
 *   list.reload(buildParams())   // 回到第一页
 */
export function usePagedList<T>(fetcher: PagedFetcher<T>, pageSize = 20) {
  const rows = ref([]) as Ref<T[]>
  const total = ref(0)
  const page = ref(1)
  const size = ref(pageSize)
  const loading = ref(false)
  const error = ref('')

  async function load(params: Record<string, unknown> = {}) {
    loading.value = true
    error.value = ''
    try {
      const data = await fetcher(params, page.value, size.value)
      rows.value = data?.items ?? []
      total.value = data?.total ?? 0
    } catch (e: unknown) {
      const err = e as { message?: string }
      error.value = err?.message || '加载失败'
      rows.value = []
      total.value = 0
    } finally {
      loading.value = false
    }
  }

  /** 条件变化后重查：回到第一页 */
  function reload(params: Record<string, unknown> = {}) {
    page.value = 1
    return load(params)
  }

  return { rows, total, page, pageSize: size, loading, error, load, reload }
}
