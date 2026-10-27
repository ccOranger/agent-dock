<template>
  <div v-loading="store.loading">
    <el-card>
      <template #header>
        <div class="head">
          <b>授权与版本</b>
          <span class="tip">离线校验；授权到期或校验失败只会关闭企业功能，对话与社区版功能不受影响</span>
        </div>
      </template>

      <el-alert v-if="store.banner" :type="store.banner.type" :title="store.banner.text" show-icon :closable="false" />

      <el-row :gutter="16" class="cards">
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic :title="'当前版本'" :value="status?.edition_label ?? '—'" />
            <p class="muted">edition = {{ status?.edition ?? '—' }}</p>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic :title="'客户'" :value="status?.customer || '社区版（未授权）'" />
            <p class="muted">合同号：{{ status?.contract_ref || '—' }}</p>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic :title="'到期时间'" :value="fmtTime(status?.expires_at)" />
            <p class="muted">签发：{{ fmtTime(status?.issued_at) }}</p>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic :title="'剩余天数'" :value="status?.days_left ?? '—'" />
            <p class="muted">{{ daysHint }}</p>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="block">
        <template #header>
          <div class="head">
            <b>企业功能位</b>
            <span class="tip">共 {{ featureKeys.length }} 项；社区版一律关闭，企业功能实现不在开源仓库</span>
          </div>
        </template>
        <el-table :data="featureRows" size="small">
          <el-table-column prop="label" label="功能" width="140" />
          <el-table-column prop="desc" label="说明" min-width="320" />
          <el-table-column label="状态" width="130">
            <template #default="{ row }">
              <el-tag v-if="row.state === 'active'" type="success" size="small">已授权</el-tag>
              <el-tag v-else-if="row.state === 'declared'" type="warning" size="small">已声明未生效</el-tag>
              <el-tag v-else type="info" size="small">未授权</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <p v-if="status?.unknown_features?.length" class="muted">
          授权文件里还有本版本不认识的功能位：{{ status.unknown_features.join('、') }}（可能是拼写错误或更高版本功能）
        </p>
      </el-card>

      <el-card shadow="never" class="block">
        <template #header>
          <div class="head">
            <b>社区版包含</b>
            <span class="tip">这些能力不设功能位，到期后依然可用</span>
          </div>
        </template>
        <ul class="scope">
          <li v-for="item in status?.community_scope ?? []" :key="item">{{ item }}</li>
        </ul>
      </el-card>

      <el-card shadow="never" class="block">
        <template #header>
          <div class="head">
            <b>授权文件</b>
            <span class="tip">换文件后点“重新加载”即可，无需重启服务</span>
          </div>
        </template>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="文件路径">{{ status?.source || '—' }}</el-descriptions-item>
          <el-descriptions-item label="是否已加载">{{ status?.configured ? '是' : '否（按社区版运行）' }}</el-descriptions-item>
          <el-descriptions-item label="部署指纹">
            {{ status?.deployment_id || '未绑定' }}
            <el-tag v-if="status?.deployment_bound" type="success" size="small" style="margin-left: 6px">已匹配</el-tag>
            <el-tag v-else type="info" size="small" style="margin-left: 6px">未校验</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="交付模式">{{ status?.mode || '—' }}</el-descriptions-item>
          <el-descriptions-item label="校验结论" :span="2">
            {{ status?.reason || '校验通过' }}
          </el-descriptions-item>
        </el-descriptions>
        <div class="actions">
          <el-button :loading="store.loading" @click="onReload">重新加载</el-button>
          <span class="muted">换授权 / 续期后无需重启；社区版对话、知识库、工具、追踪、用量报表均不受影响</span>
        </div>
      </el-card>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { useLicenseStore } from '@/stores/license'

const store = useLicenseStore()
const status = computed(() => store.status)

const featureKeys = computed(() => Object.keys(status.value?.feature_catalog ?? {}))
const featureRows = computed(() =>
  featureKeys.value.map((key) => {
    const meta = status.value?.feature_catalog[key]
    const active = status.value?.active_features.includes(key) ?? false
    const declared = status.value?.features.includes(key) ?? false
    return {
      key,
      label: meta?.label ?? key,
      desc: meta?.desc ?? '',
      state: active ? 'active' : declared ? 'declared' : 'none',
    }
  }),
)

const daysHint = computed(() => {
  const s = status.value
  if (!s) return '未读取到授权信息'
  if (s.expired) return '已到期，按社区版运行'
  if (s.days_left === null) return '无到期时间'
  return s.days_left <= s.expiring_soon_days ? `≤ ${s.expiring_soon_days} 天，请及时续期` : '授权有效'
})

/** 时间统一显示为 YYYY-MM-DD HH:mm:ss；授权文件里的时间是 UTC */
function fmtTime(value: string | null | undefined) {
  if (!value) return '—'
  const parsed = dayjs(value)
  return parsed.isValid() ? `${parsed.format('YYYY-MM-DD HH:mm:ss')} UTC` : value
}

async function onReload() {
  try {
    const next = await store.reload()
    if (next?.valid) ElMessage.success(`授权已生效：${next.edition_label}`)
    else ElMessage.warning('授权未生效，仍按社区版运行')
  } catch {
    ElMessage.error('重新加载失败，请检查授权文件权限')
  }
}

onMounted(() => store.load())
</script>

<style scoped>
.head {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.tip {
  color: #909399;
  font-size: 12px;
}
.cards {
  margin-top: 12px;
}
.muted {
  color: #909399;
  font-size: 12px;
  margin: 6px 0 0;
}
.block {
  margin-top: 12px;
}
.scope {
  margin: 0;
  padding-left: 18px;
  color: #606266;
  line-height: 1.9;
}
.actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}
</style>
