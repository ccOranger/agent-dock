<template>
  <div class="page">
    <PageHeader
      title="角色与权限"
      :count="roles.length ? `${roles.length} 个` : undefined"
    >
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" @click="openCreate">新建角色</el-button>
      </template>
    </PageHeader>

    <el-card>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
        title="读 = 浏览查询，写 = 新增修改（含读）；管理员恒为全模块读写"
      />

      <el-table :data="roles" v-loading="loading">
        <el-table-column label="角色" min-width="220">
          <template #default="{ row }">
            <div class="role-name">
              <span class="name">{{ row.name }}</span>
              <el-tag v-if="row.is_system" size="small" type="warning">内置</el-tag>
            </div>
            <div class="code-line mono">{{ row.key }}</div>
          </template>
        </el-table-column>
        <el-table-column label="说明" prop="description" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="muted">{{ row.description || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="授权模块" min-width="160">
          <template #default="{ row }">
            <span v-if="row.key === 'admin'" class="muted">全部（超级角色）</span>
            <span v-else>{{ grantCount(row) }} 项</span>
          </template>
        </el-table-column>
        <el-table-column label="在册人数" prop="user_count" width="96" />
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openPerms(row)">配置权限</el-button>
            <el-button link type="primary" size="small" @click="openRename(row)">重命名</el-button>
            <el-button link type="danger" size="small" :disabled="row.is_system" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>

  <!-- 新建 / 重命名 -->
  <el-dialog v-model="metaVisible" :title="metaEditing ? '重命名角色' : '新建角色'" width="480px">
    <el-form :model="metaForm" label-width="80px">
      <el-form-item label="标识" v-if="!metaEditing" required>
        <el-input v-model="metaForm.key" placeholder="小写字母/数字/-/_，创建后不可改" />
      </el-form-item>
      <el-form-item label="名称" required>
        <el-input v-model="metaForm.name" maxlength="64" />
      </el-form-item>
      <el-form-item label="说明">
        <el-input v-model="metaForm.description" type="textarea" :rows="2" maxlength="255" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="metaVisible = false">取消</el-button>
      <el-button type="primary" :loading="metaSubmitting" @click="submitMeta">保存</el-button>
    </template>
  </el-dialog>

  <!-- 权限矩阵 -->
  <DetailDrawer v-model="permVisible" :title="`配置权限：${permTarget?.name || ''}`" size="52%">
    <el-alert
      v-if="isAdminRole"
      type="warning"
      :closable="false"
      show-icon
      title="管理员为超级角色，恒为全模块读写，无需配置"
      style="margin-bottom: 12px"
    />
    <div class="quick-ops" v-if="!isAdminRole">
      <el-button size="small" @click="setAll('read')">全部只读</el-button>
      <el-button size="small" @click="setAll('write')">全部读写</el-button>
      <el-button size="small" @click="setAll('')">清空</el-button>
    </div>
    <div v-for="grp in groupedModules" :key="grp.name" class="perm-group">
      <div class="perm-group-title">{{ grp.name }}</div>
      <div class="perm-row" v-for="m in grp.items" :key="m.key">
        <span class="perm-label">{{ m.name }}</span>
        <el-radio-group v-model="editPerms[m.key]" :disabled="isAdminRole" size="small">
          <el-radio-button value="">无</el-radio-button>
          <el-radio-button value="read">只读</el-radio-button>
          <el-radio-button value="write">读写</el-radio-button>
        </el-radio-group>
      </div>
    </div>
    <template #footer>
      <el-button @click="permVisible = false">取消</el-button>
      <el-button type="primary" :loading="permSubmitting" :disabled="isAdminRole" @click="submitPerms">
        保存授权
      </el-button>
    </template>
  </DetailDrawer>
</template>

<script setup lang="ts">
import DetailDrawer from '@/components/DetailDrawer.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiCreateRole,
  apiDeleteRole,
  apiListMenuModules,
  apiListRoles,
  apiSetRolePermissions,
  apiUpdateRole,
} from '@/api'
import type { MenuAccess, MenuModule, Role } from '@/types'
import PageHeader from '@/components/PageHeader.vue'

const roles = ref<Role[]>([])
const modules = ref<MenuModule[]>([])
const loading = ref(false)

// ===== 新建 / 重命名 =====
const metaVisible = ref(false)
const metaEditing = ref(false)
const metaSubmitting = ref(false)
const metaTarget = ref<Role | null>(null)
const metaForm = reactive({ key: '', name: '', description: '' })

// ===== 权限矩阵 =====
const permVisible = ref(false)
const permSubmitting = ref(false)
const permTarget = ref<Role | null>(null)
const editPerms = reactive<Record<string, MenuAccess | ''>>({})
const isAdminRole = computed(() => permTarget.value?.key === 'admin')

// 按 group 分块渲染矩阵
const groupedModules = computed(() => {
  const map = new Map<string, MenuModule[]>()
  for (const m of modules.value) {
    if (!map.has(m.group)) map.set(m.group, [])
    map.get(m.group)!.push(m)
  }
  return [...map.entries()].map(([name, items]) => ({ name, items }))
})

function grantCount(row: Role) {
  return Object.keys(row.permissions || {}).length
}

async function load() {
  loading.value = true
  try {
    roles.value = await apiListRoles()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载角色失败')
  } finally {
    loading.value = false
  }
}

async function loadModules() {
  try {
    modules.value = await apiListMenuModules()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载菜单模块失败')
  }
}

function openCreate() {
  metaEditing.value = false
  metaTarget.value = null
  metaForm.key = ''
  metaForm.name = ''
  metaForm.description = ''
  metaVisible.value = true
}

function openRename(row: Role) {
  metaEditing.value = true
  metaTarget.value = row
  metaForm.key = row.key
  metaForm.name = row.name
  metaForm.description = row.description || ''
  metaVisible.value = true
}

async function submitMeta() {
  if (!metaEditing.value && !/^[a-z0-9][a-z0-9_-]*$/.test(metaForm.key)) {
    ElMessage.warning('标识需为 2-32 位小写字母/数字/-/_，且以字母或数字开头')
    return
  }
  if (!metaForm.name.trim()) {
    ElMessage.warning('名称必填')
    return
  }
  metaSubmitting.value = true
  try {
    if (metaEditing.value && metaTarget.value) {
      await apiUpdateRole(metaTarget.value.id, {
        name: metaForm.name.trim(),
        description: metaForm.description.trim() || null,
      })
    } else {
      await apiCreateRole({
        key: metaForm.key.trim(),
        name: metaForm.name.trim(),
        description: metaForm.description.trim() || null,
      })
    }
    ElMessage.success('已保存')
    metaVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    metaSubmitting.value = false
  }
}

function openPerms(row: Role) {
  permTarget.value = row
  for (const m of modules.value) editPerms[m.key] = row.permissions?.[m.key] || ''
  permVisible.value = true
}

function setAll(level: MenuAccess | '') {
  for (const m of modules.value) editPerms[m.key] = level
}

async function submitPerms() {
  if (!permTarget.value) return
  const payload: Record<string, MenuAccess> = {}
  for (const m of modules.value) {
    const v = editPerms[m.key]
    if (v) payload[m.key] = v
  }
  permSubmitting.value = true
  try {
    await apiSetRolePermissions(permTarget.value.id, payload)
    ElMessage.success('授权已保存；被影响人员需重新登录生效')
    permVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    permSubmitting.value = false
  }
}

async function remove(row: Role) {
  try {
    await ElMessageBox.confirm(`确认删除角色「${row.name}」？该操作会解除其与人员的绑定。`, '删除角色', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await apiDeleteRole(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

onMounted(() => {
  loadModules()
  load()
})
</script>

<style scoped>
.role-name {
  display: flex;
  align-items: center;
  gap: 6px;
}

.role-name .name {
  font-weight: 600;
}

.code-line {
  font-size: 12px;
}

/* 权限矩阵 */
.quick-ops {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.perm-group {
  margin-bottom: 14px;
}

.perm-group-title {
  margin-bottom: 6px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--cs-border-light);
  font-weight: 600;
  color: var(--cs-text-1);
}

.perm-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
}

.perm-label {
  color: var(--cs-text-2);
}
</style>
