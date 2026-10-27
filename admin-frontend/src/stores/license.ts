import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { apiLicenseStatus, apiReloadLicense } from '@/api'
import type { LicenseStatus } from '@/types'

/**
 * 授权状态（进程内单例，启动时算一次）。
 *
 * 后台只在 admin 登录时加载：/admin/license 是 admin 接口，agent 调用会 403。
 * 降级不是错误状态——社区版开箱即用，只有“放了授权文件却没生效”才提示。
 */
export const useLicenseStore = defineStore('license', () => {
  const status = ref<LicenseStatus | null>(null)
  const loading = ref(false)

  const isEnterprise = computed(() => status.value?.edition === 'enterprise')

  /** 功能位开关：企业功能页面在私有仓库，开源仓库只提供挂载点 */
  function can(feature: string): boolean {
    return status.value?.active_features.includes(feature) ?? false
  }

  async function load(force = false): Promise<void> {
    if (status.value && !force) return
    loading.value = true
    try {
      status.value = await apiLicenseStatus()
    } catch {
      // 授权信息拿不到不该影响后台使用：保持 null，页面上给出“读取失败”
      status.value = null
    } finally {
      loading.value = false
    }
  }

  /** 换授权文件后调用，无需重启服务 */
  async function reload(): Promise<LicenseStatus | null> {
    loading.value = true
    try {
      status.value = await apiReloadLicense()
      return status.value
    } finally {
      loading.value = false
    }
  }

  /** 顶部横幅：只提示需要处理的情况，社区版正常使用时不打扰 */
  const banner = computed<{ type: 'warning' | 'error'; text: string } | null>(() => {
    const s = status.value
    if (!s) return null
    if (s.expired) {
      return {
        type: 'error',
        text: `授权已于 ${s.expires_at} 到期，当前按社区版运行（对话与社区功能不受影响）；续期后点“重新加载”即可恢复。`,
      }
    }
    if (!s.valid && s.configured) {
      return { type: 'error', text: `授权文件未生效，当前按社区版运行：${s.reason}` }
    }
    if (s.valid && s.expiring_soon) {
      return {
        type: 'warning',
        text: `授权将在 ${s.days_left} 天后到期（${s.expires_at}），请及时续期。`,
      }
    }
    return null
  })

  return { status, loading, isEnterprise, can, load, reload, banner }
})
