<template>
  <div class="page">
    <PageHeader
      title="人员与配额"
      description="管理后台账号、角色集合与资源创建配额；配额只统计本人创建的资源，删除即释放。"
      :count="staff.length ? `${staff.length} 人` : undefined"
    >
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" @click="openCreate">新建人员</el-button>
      </template>
    </PageHeader>

    <el-card>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="配额只统计「当前登录人自己创建」的资源，删除即释放；留空表示不限，0 表示禁止创建"
        description="默认场景、seed 的代码 Agent 与演示数据 created_by 为空，不占用任何人的额度。管理员自身默认也不受限，但可以被另一个管理员配置额度——为了避免把自己锁死，后端不允许修改自己的配额、角色与启停状态。"
        style="margin-bottom: 12px"
      />

      <el-table :data="staff" v-loading="loading">
        <el-table-column label="人员" min-width="180">
          <template #default="{ row }">
            <div class="staff-name">
              <span class="name">{{ row.display_name || row.username }}</span>
              <el-tag v-if="row.id === auth.user?.id" size="small" type="success">当前登录</el-tag>
              <el-tag v-if="!row.is_active" size="small" type="danger">已停用</el-tag>
            </div>
            <div class="code-line mono">{{ row.username }}</div>
          </template>
        </el-table-column>
        <el-table-column label="角色" min-width="160">
          <template #default="{ row }">
            <el-tag
              v-for="k in row.roles && row.roles.length ? row.roles : [row.role]"
              :key="k"
              size="small"
              class="role-tag"
              :type="k === 'admin' ? 'warning' : 'info'"
            >
              {{ roleName(k) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          v-for="item in quotaColumns"
          :key="item.key"
          :label="item.label"
          width="150"
        >
          <template #default="{ row }">
            <span :class="quotaClass(row, item.key)">
              {{ row.used[item.usageKey] }} / {{ quotaLimitText(row, item.key) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch
              v-model="row.is_active"
              :disabled="row.id === auth.user?.id"
              @change="() => onToggleActive(row)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" size="small" @click="openPassword(row)">重置密码</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>

  <el-dialog v-model="dialogVisible" :title="editing ? `编辑人员：${form.username}` : '新建人员'" width="560px">
    <el-form :model="form" label-width="110px">
      <el-form-item label="用户名" required>
        <el-input v-model="form.username" :disabled="editing" placeholder="登录名，3-64 位字母/数字/._-" />
      </el-form-item>
      <el-form-item v-if="!editing" label="初始密码" required>
        <el-input v-model="form.password" type="password" show-password placeholder="至少 8 位" />
      </el-form-item>
      <el-form-item label="展示名">
        <el-input v-model="form.display_name" maxlength="64" placeholder="可选，列表与工单里显示的名字" />
      </el-form-item>
      <el-form-item label="角色">
        <el-select
          v-model="form.roles"
          multiple
          :disabled="isSelfEditing"
          placeholder="选择该人员的角色集合（可多选）"
          style="width: 100%"
        >
          <el-option v-for="r in roleOptions" :key="r.key" :label="r.name" :value="r.key" />
        </el-select>
        <div class="rule-tip">
          一人可挂多个角色，菜单/读写权限取并集；含「管理员」即超级权限。{{
            isSelfEditing ? '不能修改自己的角色集合，避免把自己锁死。' : ''
          }}
        </div>
      </el-form-item>

      <el-divider content-position="left">创建配额</el-divider>
      <div class="rule-tip quota-tip">
        留空 = 不限；填 0 = 禁止创建；正数 = 上限。删除资源立即释放额度，编辑不消耗。
      </div>
      <el-form-item v-for="item in quotaColumns" :key="item.key" :label="item.label">
        <el-input-number
          v-model="form.quota[item.key]"
          :min="0"
          :max="item.max"
          :disabled="isSelfEditing"
          :placeholder="'不限'"
          controls-position="right"
          style="width: 180px"
        />
        <span class="muted small quota-used">已用 {{ editing && editRow ? editRow.used[item.usageKey] : 0 }}</span>
      </el-form-item>
      <el-form-item v-if="isSelfEditing">
        <span class="muted small">不能修改自己的配额：请由其他管理员操作，或换一个账号登录</span>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="passwordVisible" :title="`重置密码：${passwordTarget?.username || ''}`" width="460px">
    <el-form label-width="90px">
      <el-form-item label="新密码" required>
        <el-input v-model="newPassword" type="password" show-password placeholder="至少 8 位" />
      </el-form-item>
      <el-form-item>
        <span class="muted small">重置后该账号现有登录态仍有效，直到 token 过期</span>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="passwordVisible = false">取消</el-button>
      <el-button type="primary" :loading="passwordSaving" @click="submitPassword">确认重置</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiCreateStaff, apiListRoles, apiListStaff, apiResetStaffPassword, apiUpdateStaff } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { STAFF_QUOTA_LABELS } from '@/types'
import type { StaffCreatePayload, StaffMember, StaffQuota, StaffUpdatePayload } from '@/types'
import PageHeader from '@/components/PageHeader.vue'

const auth = useAuthStore()

const staff = ref<StaffMember[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref(false)
const submitting = ref(false)
const editRow = ref<StaffMember | null>(null)

// 角色下拉选项：来自 RBAC，含内置 admin/agent 与自定义角色
const roleOptions = ref<{ key: string; name: string }[]>([])

const passwordVisible = ref(false)
const passwordSaving = ref(false)
const passwordTarget = ref<StaffMember | null>(null)
const newPassword = ref('')

// 三列配额：key 是后端 quota 的键，usageKey 是同一资源的已用数量键
// 上限与后端 quota_service.MAX_LIMIT 对齐（1000）；调小会让已有大额配额被动降额
const quotaColumns = [
  { key: 'max_scenes' as const, usageKey: 'scenes' as const, max: 1000 },
  { key: 'max_agents' as const, usageKey: 'agents' as const, max: 1000 },
  { key: 'max_knowledge_bases' as const, usageKey: 'knowledge_bases' as const, max: 1000 },
].map((item) => ({ ...item, label: STAFF_QUOTA_LABELS[item.key] }))

/** 表单里 null 表示“不限”，el-input-number 清空后会给 undefined/null，提交时统一剔除 */
interface StaffForm {
  username: string
  password: string
  display_name: string
  roles: string[]
  quota: Record<keyof StaffQuota, number | null>
}

const form = reactive<StaffForm>({
  username: '',
  password: '',
  display_name: '',
  roles: ['agent'],
  quota: { max_scenes: null, max_agents: null, max_knowledge_bases: null },
})

function roleName(key: string) {
  return roleOptions.value.find((r) => r.key === key)?.name || key
}

// 自己不能改配额/角色/启停：与后端保护规则同源，前端先禁用减少无效提交
const isSelfEditing = computed(() => editing.value && editRow.value?.id === auth.user?.id)

function quotaLimitText(row: StaffMember, key: keyof StaffQuota) {
  const limit = row.quota?.[key]
  if (limit === null || limit === undefined) return '不限'
  return String(limit)
}

/** 达上限标红、超 80% 标黄；不限时不着色 */
function quotaClass(row: StaffMember, key: keyof StaffQuota) {
  const limit = row.quota?.[key]
  if (limit === null || limit === undefined || limit <= 0) return ''
  const usageKey = quotaColumns.find((c) => c.key === key)?.usageKey
  const used = usageKey ? row.used[usageKey] : 0
  if (used >= limit) return 'quota-full'
  if (used * 5 >= limit * 4) return 'quota-warn'
  return ''
}

async function load() {
  loading.value = true
  try {
    staff.value = await apiListStaff()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载人员列表失败')
  } finally {
    loading.value = false
  }
}

async function loadRoles() {
  try {
    const roles = await apiListRoles()
    roleOptions.value = roles.map((r) => ({ key: r.key, name: r.name }))
  } catch {
    /* 角色选项加载失败不阻断人员页：退回内置两个 key */
    roleOptions.value = [
      { key: 'agent', name: '客服坐席' },
      { key: 'admin', name: '管理员' },
    ]
  }
}

function resetForm() {
  form.username = ''
  form.password = ''
  form.display_name = ''
  form.roles = ['agent']
  form.quota = { max_scenes: null, max_agents: null, max_knowledge_bases: null }
}

function openCreate() {
  editing.value = false
  editRow.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: StaffMember) {
  editing.value = true
  editRow.value = row
  form.username = row.username
  form.password = ''
  form.display_name = row.display_name || ''
  form.roles = row.roles && row.roles.length ? [...row.roles] : [row.role]
  form.quota = {
    max_scenes: row.quota?.max_scenes ?? null,
    max_agents: row.quota?.max_agents ?? null,
    max_knowledge_bases: row.quota?.max_knowledge_bases ?? null,
  }
  dialogVisible.value = true
}

/** null/undefined 的键不出现在 payload 里：后端按“缺失 = 不限”处理 */
function quotaPayload(): StaffQuota {
  const out: StaffQuota = {}
  for (const item of quotaColumns) {
    const value = form.quota[item.key]
    if (value !== null && value !== undefined) out[item.key] = value
  }
  return out
}

async function submit() {
  if (!editing.value) {
    if (!form.username.trim() || !form.password) {
      ElMessage.warning('用户名与初始密码都要填写')
      return
    }
    if (form.password.length < 8) {
      ElMessage.warning('密码至少 8 位')
      return
    }
  }
  submitting.value = true
  try {
    const primary = form.roles.includes('admin') ? 'admin' : 'agent'
    if (editing.value && editRow.value) {
      const payload: StaffUpdatePayload = {
        display_name: form.display_name.trim() || null,
        // 自己改自己时后端会 400，角色与配额干脆不传
        ...(isSelfEditing.value ? {} : { roles: form.roles, quota: quotaPayload() }),
      }
      await apiUpdateStaff(editRow.value.id, payload)
    } else {
      const payload: StaffCreatePayload = {
        username: form.username.trim(),
        password: form.password,
        role: primary,
        roles: form.roles.length ? form.roles : [primary],
        display_name: form.display_name.trim() || null,
        quota: quotaPayload(),
      }
      await apiCreateStaff(payload)
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

async function onToggleActive(row: StaffMember) {
  try {
    await apiUpdateStaff(row.id, { is_active: row.is_active })
    ElMessage.success(`${row.username} 已${row.is_active ? '启用' : '停用'}`)
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
    await load()
  }
}

function openPassword(row: StaffMember) {
  passwordTarget.value = row
  newPassword.value = ''
  passwordVisible.value = true
}

async function submitPassword() {
  if (!passwordTarget.value) return
  if (newPassword.value.length < 8) {
    ElMessage.warning('密码至少 8 位')
    return
  }
  passwordSaving.value = true
  try {
    await apiResetStaffPassword(passwordTarget.value.id, newPassword.value)
    ElMessage.success('密码已重置')
    passwordVisible.value = false
  } catch (e: any) {
    ElMessage.error(e?.message || '重置失败')
  } finally {
    passwordSaving.value = false
  }
}

onMounted(() => {
  load()
  loadRoles()
})
</script>

<style scoped>
.staff-name {
  display: flex;
  align-items: center;
  gap: 6px;
}

.staff-name .name {
  font-weight: 600;
}

.role-tag {
  margin: 2px 4px 2px 0;
}

.code-line {
  margin-top: 2px;
  font-size: 12px;
}

.quota-tip {
  margin: -4px 0 10px;
}

.quota-used {
  margin-left: 10px;
}

/* 配额水线：达上限标红、过 80% 标黄 */
.quota-warn {
  color: var(--cs-warning);
  font-weight: 600;
}

.quota-full {
  color: var(--cs-danger);
  font-weight: 600;
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
</style>
