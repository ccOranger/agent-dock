import { ref } from 'vue'
import { defineStore } from 'pinia'

import { apiListPacks, apiTogglePack } from '@/api'
import type { ScenePack } from '@/types'

/**
 * 场景包 store：运营台导航按包声明的 features 显隐（如 tickets）。
 * 接口失败时 loaded=false，hasFeature 回退为全部可见，避免异常把导航整块隐藏。
 */
export const usePacksStore = defineStore('packs', () => {
  const packs = ref<ScenePack[]>([])
  const loading = ref(false)
  const loaded = ref(false)
  const attempted = ref(false)

  async function fetchPacks(force = false) {
    if (loading.value) return
    if (attempted.value && !force) return
    loading.value = true
    try {
      packs.value = await apiListPacks()
      loaded.value = true
    } catch {
      packs.value = []
      loaded.value = false
    } finally {
      attempted.value = true
      loading.value = false
    }
  }

  /** 未成功加载时回退 true（全部可见），加载成功后按启用包的 features 判定。 */
  function hasFeature(feature: string) {
    if (!loaded.value) return true
    return packs.value.some((p) => p.enabled && p.features.includes(feature))
  }

  async function togglePack(name: string, enabled: boolean) {
    const updated = await apiTogglePack(name, enabled)
    const idx = packs.value.findIndex((p) => p.name === name)
    if (idx >= 0) {
      packs.value[idx] = updated
    } else {
      packs.value.push(updated)
    }
    loaded.value = true
    return updated
  }

  return { packs, loading, loaded, attempted, fetchPacks, hasFeature, togglePack }
})
