<template>
  <div class="page">
    <PageHeader
      title="接入方管理"
      :count="integrations.length ? `${integrations.length} 个` : undefined"
    >
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" @click="openCreate">新建接入方</el-button>
      </template>
    </PageHeader>

    <el-card>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="Key 归接入方；停用接入方会让名下所有 Key 立即失效"
        style="margin-bottom: 12px"
      />

      <el-table :data="integrations" v-loading="loading">
        <el-table-column label="接入方" min-width="200">
          <template #default="{ row }">
            <div class="name-line">
              <span class="name">{{ row.name }}</span>
              <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">
                {{ row.status === 'active' ? '启用' : '已停用' }}
              </el-tag>
            </div>
            <div class="muted small">{{ row.description || '—' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="公开标识" min-width="220">
          <template #default="{ row }">
            <code class="public-code mono">{{ row.public_code }}</code>
            <el-button link size="small" @click="copyText(row.public_code)">复制</el-button>
          </template>
        </el-table-column>
        <el-table-column label="授权场景" min-width="240">
          <template #default="{ row }">
            <el-tag
              v-for="scene in row.scenes"
              :key="scene.id"
              size="small"
              type="info"
              class="scene-tag"
            >
              {{ scene.display_name }}
            </el-tag>
            <span v-if="!row.scenes.length" class="muted">未授权任何场景</span>
          </template>
        </el-table-column>
        <el-table-column label="Key" width="96">
          <template #default="{ row }">
            <span :class="{ muted: !row.key_count }">{{ row.key_count }} 把</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="232" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" size="small" @click="openKeys(row)">Key 管理</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>

  <el-dialog
    v-model="dialogVisible"
    :title="editing ? `编辑接入方：${form.name}` : '新建接入方'"
    width="620px"
  >
    <el-form :model="form" label-width="100px">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" maxlength="64" placeholder="如：商城官网后端" />
        <div class="rule-tip">名称唯一，用于后台辨认调用方。</div>
      </el-form-item>
      <el-form-item label="说明">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          maxlength="500"
          placeholder="这个接入方承担哪部分流量、由哪个团队维护"
        />
      </el-form-item>
      <el-form-item v-if="editing" label="公开标识">
        <code class="public-code">{{ form.publicCode }}</code>
        <el-button link size="small" @click="copyText(form.publicCode)">复制</el-button>
        <div class="rule-tip">
          嵌入页用 data-integration 携带；只做匿名访客归属与统计，不是密钥。
        </div>
      </el-form-item>
      <el-form-item v-if="editing" label="状态">
        <el-select v-model="form.status" style="width: 100%">
          <el-option label="启用" value="active" />
          <el-option label="停用（名下 Key 立即失效）" value="disabled" />
        </el-select>
      </el-form-item>
      <el-form-item label="授权场景">
        <el-select
          v-model="form.sceneIds"
          multiple
          filterable
          style="width: 100%"
          placeholder="选择该接入方可访问的场景"
        >
          <el-option
            v-for="scene in scenes"
            :key="scene.id"
            :label="sceneOptionLabel(scene)"
            :value="scene.id"
          />
        </el-select>
        <div class="rule-tip">
          未授权的场景用该接入方的 Key 调用会返回 403；不选等于暂不授权。
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
    </template>
  </el-dialog>

  <DetailDrawer v-model="keysVisible" :title="keysTitle" size="620px" @closed="issuedKey = ''">
    <div v-loading="keysLoading" class="keys-body">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="Key 明文只在签发时出现一次，库里只存 sha256"
        description="轮换时先签发新 Key、把服务端切过去，确认流量正常后再吊销旧 Key，避免调用中断。"
        style="margin-bottom: 12px"
      />

      <div class="keys-ops">
        <el-button type="primary" size="small" :loading="issuing" @click="issueKey">
          签发新 Key
        </el-button>
      </div>

      <div v-if="issuedKey" class="issued-key">
        <div class="issued-title">新 Key 明文（关闭抽屉后无法再次查看）</div>
        <code>{{ issuedKey }}</code>
        <el-button link size="small" @click="copyText(issuedKey)">复制</el-button>
      </div>

      <el-table :data="keys" size="small" v-loading="keysLoading">
        <el-table-column label="尾号" width="152">
          <template #default="{ row }">
            <code>…{{ row.hint }}</code>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="168">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="96">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click="revokeKey(row)">吊销</el-button>
          </template>
        </el-table-column>
      </el-table>
      <span v-if="!keys.length && !keysLoading" class="muted">
        还没有 Key，签发后把明文填到接入方服务端的环境变量。
      </span>
    </div>
    <template #footer>
      <el-button @click="keysVisible = false">关闭</el-button>
    </template>
  </DetailDrawer>
</template>

<script setup lang="ts">
import DetailDrawer from '@/components/DetailDrawer.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiCreateIntegration,
  apiDeleteIntegration,
  apiGetIntegration,
  apiIssueIntegrationKey,
  apiListIntegrations,
  apiListScenes,
  apiRevokeIntegrationKey,
  apiUpdateIntegration,
} from '@/api'
import type {
  Integration,
  IntegrationCreatePayload,
  IntegrationKeyInfo,
  IntegrationStatus,
  IntegrationUpdatePayload,
  Scene,
} from '@/types'
import PageHeader from '@/components/PageHeader.vue'

const integrations = ref<Integration[]>([])
const scenes = ref<Scene[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref(false)
const submitting = ref(false)

interface IntegrationForm {
  id: string
  name: string
  publicCode: string
  description: string
  status: IntegrationStatus
  sceneIds: string[]
}

const form = reactive<IntegrationForm>({
  id: '',
  name: '',
  publicCode: '',
  description: '',
  status: 'active',
  sceneIds: [],
})

function sceneOptionLabel(scene: Scene) {
  return `${scene.display_name}（${scene.code}）${scene.status !== 'active' ? ' · 已停用' : ''}`
}

async function load() {
  loading.value = true
  try {
    const [rows, sceneRows] = await Promise.all([apiListIntegrations(), apiListScenes()])
    integrations.value = rows
    scenes.value = sceneRows
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = false
  form.id = ''
  form.name = ''
  form.publicCode = ''
  form.description = ''
  form.status = 'active'
  form.sceneIds = []
  dialogVisible.value = true
}

function openEdit(row: Integration) {
  editing.value = true
  form.id = row.id
  form.name = row.name
  form.publicCode = row.public_code
  form.description = row.description || ''
  form.status = row.status
  form.sceneIds = row.scenes.map((s) => s.id)
  dialogVisible.value = true
}

async function submit() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写接入方名称')
    return
  }
  submitting.value = true
  try {
    const base = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      scene_ids: form.sceneIds,
    }
    if (editing.value) {
      const payload: IntegrationUpdatePayload = { ...base, status: form.status }
      await apiUpdateIntegration(form.id, payload)
    } else {
      await apiCreateIntegration(base as IntegrationCreatePayload)
    }
    ElMessage.success('已保存')
    dialogVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

async function remove(row: Integration) {
  try {
    await ElMessageBox.confirm(
      `删除后该接入方的全部 Key 与场景授权立即失效且不可恢复。已有访客会话的接入方只能停用。确认删除「${row.name}」？`,
      '删除接入方',
      { type: 'warning' },
    )
    await apiDeleteIntegration(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

// ===== Key 管理 =====
const keysVisible = ref(false)
const keysLoading = ref(false)
const issuing = ref(false)
const keyScene = ref<Integration | null>(null)
const keys = ref<IntegrationKeyInfo[]>([])
const issuedKey = ref('')

const keysTitle = computed(() =>
  keyScene.value ? `Key 管理：${keyScene.value.name}` : 'Key 管理',
)

async function loadKeys(id: string) {
  keysLoading.value = true
  try {
    const detail = await apiGetIntegration(id)
    keyScene.value = detail
    keys.value = detail.keys || []
    // 同步列表上的 Key 计数，关掉抽屉后不留旧数字
    const row = integrations.value.find((x) => x.id === id)
    if (row) row.key_count = detail.key_count
  } catch (e: any) {
    ElMessage.error(e?.message || '读取 Key 失败')
  } finally {
    keysLoading.value = false
  }
}

async function openKeys(row: Integration) {
  keyScene.value = row
  keys.value = []
  issuedKey.value = ''
  keysVisible.value = true
  await loadKeys(row.id)
}

async function issueKey() {
  if (!keyScene.value) return
  issuing.value = true
  try {
    const result = await apiIssueIntegrationKey(keyScene.value.id)
    issuedKey.value = result.api_key
    ElMessage.success('已签发，请立即复制明文')
    await loadKeys(keyScene.value.id)
  } catch (e: any) {
    ElMessage.error(e?.message || '签发失败')
  } finally {
    issuing.value = false
  }
}

async function revokeKey(key: IntegrationKeyInfo) {
  if (!keyScene.value) return
  try {
    await ElMessageBox.confirm(
      `吊销后使用该 Key（尾号 ${key.hint}）的服务端调用立即返回 401，且不可恢复。确认吊销？`,
      '吊销 Key',
      { type: 'warning' },
    )
    await apiRevokeIntegrationKey(keyScene.value.id, key.id)
    ElMessage.success('已吊销')
    await loadKeys(keyScene.value.id)
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

function copyText(text: string) {
  navigator.clipboard?.writeText(text)
  ElMessage.success('已复制')
}

function formatTime(value?: string | null) {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

onMounted(load)
</script>

<style scoped>
.name-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.name {
  font-weight: 600;
}

.public-code {
  font-size: 12px;
  word-break: break-all;
}

.scene-tag {
  margin-right: 4px;
  margin-bottom: 2px;
}

.small {
  font-size: 12px;
}

.rule-tip {
  margin-top: 2px;
  color: var(--cs-text-3);
  font-size: 12px;
  line-height: 1.5;
}

.keys-body {
  min-height: 160px;
}

.keys-ops {
  margin-bottom: 10px;
}

/* 新签发的 Key 明文：需要强提醒，用告警底色 */
.issued-key {
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px solid var(--cs-warning-line);
  border-radius: var(--cs-radius-sm);
  background: var(--cs-warning-soft);
}

.issued-title {
  margin-bottom: 4px;
  color: var(--cs-warning-text);
  font-size: 12px;
}

.issued-key code {
  word-break: break-all;
}
</style>
