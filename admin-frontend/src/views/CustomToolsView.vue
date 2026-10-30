<template>
  <div class="page">
    <PageHeader
      title="自定义工具"
      description="后台登记代码目录中的工具，供动态 Agent 绑定；启用状态即时生效。"
      :count="tools.length ? `${tools.length} 个` : undefined"
    >
      <template #actions>
        <el-button :loading="loading" @click="load">刷新目录</el-button>
      </template>
    </PageHeader>

    <el-card>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="工具实现位于代码目录 app/tools/custom/（基类 BaseTool 同目录），此处仅做后台登记、启用/禁用，并供动态 Agent 绑定；新增工具需先在该目录开发，并在 app/tools/custom_catalog.py 的 _TOOL_CLASSES 中登记，重启后端后刷新。"
        style="margin-bottom: 12px"
      />

      <el-table :data="tools" v-loading="loading">
        <el-table-column type="expand">
          <template #default="{ row }">
            <pre class="text-block mono">{{ JSON.stringify(row.parameters, null, 2) }}</pre>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" width="180" />
        <el-table-column label="描述" show-overflow-tooltip>
          <template #default="{ row }">{{ row.display_description || row.description }}</template>
        </el-table-column>
        <el-table-column label="参数" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="muted">{{ paramNames(row.parameters) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="登记状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.registered ? 'success' : 'info'" size="small">
              {{ row.registered ? '已登记' : '未登记' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" :disabled="!row.registered" @change="() => onToggle(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button v-if="!row.registered" link type="primary" size="small" @click="openRegister(row)">登记</el-button>
            <el-button v-else link type="danger" size="small" @click="unregister(row)">取消登记</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>

  <el-dialog v-model="dialogVisible" title="登记自定义工具" width="480px">
    <el-form :model="form" label-width="110px">
      <el-form-item label="名称">
        <el-input :model-value="form.name" disabled />
      </el-form-item>
      <el-form-item label="代码描述">
        <div class="desc-text">{{ currentDescription }}</div>
      </el-form-item>
      <el-form-item label="覆盖显示描述">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          placeholder="可选，仅用于后台展示；留空则显示代码描述"
        />
      </el-form-item>
      <el-form-item label="登记后启用">
        <el-switch v-model="form.enabled" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submitRegister">登记</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiDeleteCustomTool,
  apiListCustomTools,
  apiRegisterCustomTool,
  apiToggleCustomTool,
} from '@/api'
import type { CustomToolSpec } from '@/types'
import PageHeader from '@/components/PageHeader.vue'

const tools = ref<CustomToolSpec[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const submitting = ref(false)

const form = reactive<{ name: string; description: string; enabled: boolean }>({
  name: '',
  description: '',
  enabled: true,
})
const currentDescription = ref('')

function paramNames(params: Record<string, any>): string {
  const props = params?.properties
  if (!props) return '—'
  const names = Object.keys(props)
  return names.length ? names.join('、') : '无参数'
}

async function load() {
  loading.value = true
  try {
    tools.value = await apiListCustomTools()
  } finally {
    loading.value = false
  }
}

function openRegister(row: CustomToolSpec) {
  form.name = row.name
  form.description = ''
  form.enabled = true
  currentDescription.value = row.description
  dialogVisible.value = true
}

async function submitRegister() {
  submitting.value = true
  try {
    await apiRegisterCustomTool({
      name: form.name,
      description: form.description || undefined,
      enabled: form.enabled,
    })
    ElMessage.success(`已登记：${form.name}`)
    dialogVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '登记失败')
  } finally {
    submitting.value = false
  }
}

async function onToggle(row: CustomToolSpec) {
  try {
    await apiToggleCustomTool(row.name, row.enabled)
    ElMessage.success(`${row.name} 已${row.enabled ? '启用' : '禁用'}`)
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
    await load()
  }
}

async function unregister(row: CustomToolSpec) {
  try {
    await ElMessageBox.confirm(`确认取消登记「${row.name}」？`, '取消登记', { type: 'warning' })
    await apiDeleteCustomTool(row.name)
    ElMessage.success('已取消登记')
    await load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

onMounted(load)
</script>

<style scoped>
.desc-text {
  color: var(--cs-text-2);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
}

/* 参数 schema 展开行：等宽字体 + 柔和底 */
.text-block.mono {
  font-size: 12px;
  line-height: 1.55;
  word-break: break-all;
}
</style>
