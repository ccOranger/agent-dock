<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <b>MCP 服务管理</b>
        <el-button type="primary" size="small" @click="openCreate">新增 MCP 服务</el-button>
      </div>
    </template>

    <el-table :data="servers" v-loading="loading">
      <el-table-column prop="name" label="名称" width="180" />
      <el-table-column label="传输" width="90">
        <template #default="{ row }">
          <el-tag size="small">{{ row.config.transport }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="端点 / 命令" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.config.transport === 'stdio'">
            {{ row.config.command }} {{ (row.config.args || []).join(' ') }}
          </span>
          <span v-else>{{ row.config.endpoint }}</span>
        </template>
      </el-table-column>
      <el-table-column label="被引用 Agent" min-width="160">
        <template #default="{ row }">
          <el-tag v-for="a in row.used_by_agents" :key="a" size="small" type="info" style="margin-right: 4px">
            {{ a }}
          </el-tag>
          <span v-if="!row.used_by_agents.length" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="() => onToggle(row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button link type="success" size="small" @click="openTools(row)">查看工具</el-button>
          <el-button link type="warning" size="small" @click="test(row)">测试</el-button>
          <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-dialog v-model="dialogVisible" :title="editing ? '编辑 MCP 服务' : '新增 MCP 服务'" width="560px">
    <el-form :model="form" label-width="120px">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" :disabled="editing" placeholder="字母/数字/下划线，如 mcp_logistics" />
      </el-form-item>
      <el-form-item label="传输类型">
        <el-select v-model="form.transport">
          <el-option label="sse" value="sse" />
          <el-option label="http" value="http" />
          <el-option label="stdio" value="stdio" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="form.transport !== 'stdio'" label="Endpoint" required>
        <el-input v-model="form.endpoint" placeholder="http://host:port/sse" />
      </el-form-item>
      <template v-else>
        <el-form-item label="命令">
          <el-input v-model="form.command" placeholder="留空 = 使用后端运行环境的 Python 解释器（推荐）" />
        </el-form-item>
        <el-form-item label="参数">
          <el-input v-model="argsText" placeholder="相对 backend 根的服务脚本，如 mcp_servers/logistics_server.py" />
        </el-form-item>
      </template>
      <el-form-item label="超时(秒)">
        <el-input-number v-model="form.timeout_seconds" :min="1" :max="300" />
      </el-form-item>
      <el-form-item label="启用">
        <el-switch v-model="form.enabled" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="toolsDialogVisible" :title="`工具列表 · ${toolsServerName}`" width="720px">
    <div v-loading="toolsLoading" element-loading-text="连接 MCP 服务中…">
      <el-alert v-if="toolsError" :title="`获取失败：${toolsError}`" type="error" :closable="false" show-icon />
      <template v-else>
        <div class="tools-summary">共 {{ tools.length }} 个工具（展开行可查看参数 Schema）</div>
        <el-table :data="tools" size="small" max-height="440">
          <el-table-column type="expand">
            <template #default="{ row }">
              <pre class="schema-pre">{{ JSON.stringify(row.input_schema, null, 2) }}</pre>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="工具名" width="260" />
          <el-table-column prop="description" label="描述" show-overflow-tooltip />
        </el-table>
        <div v-if="!tools.length && !toolsLoading" class="muted">未发现工具</div>
      </template>
    </div>
    <template #footer>
      <el-button @click="toolsDialogVisible = false">关闭</el-button>
      <el-button type="primary" :loading="toolsLoading" @click="loadTools(toolsServerId)">刷新</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiCreateMcpServer,
  apiDeleteMcpServer,
  apiListMcpServers,
  apiListMcpTools,
  apiTestMcpServer,
  apiUpdateMcpServer,
} from '@/api'
import type { MCPServer, MCPServerPayload, MCPServerTool } from '@/types'

const servers = ref<MCPServer[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const submitting = ref(false)
const editing = ref(false)
const editingId = ref<string>('')
const argsText = ref('')

const toolsDialogVisible = ref(false)
const toolsLoading = ref(false)
const toolsError = ref('')
const toolsServerName = ref('')
const toolsServerId = ref('')
const tools = ref<MCPServerTool[]>([])

const emptyForm = (): MCPServerPayload => ({
  name: '',
  transport: 'sse',
  endpoint: '',
  command: '',
  args: [],
  timeout_seconds: 15,
  enabled: true,
})
const form = reactive<MCPServerPayload>(emptyForm())

async function load() {
  loading.value = true
  try {
    servers.value = await apiListMcpServers()
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = false
  editingId.value = ''
  Object.assign(form, emptyForm())
  argsText.value = ''
  dialogVisible.value = true
}

function openEdit(row: MCPServer) {
  editing.value = true
  editingId.value = row.id
  Object.assign(form, {
    name: row.name,
    transport: row.config.transport,
    endpoint: row.config.endpoint ?? '',
    command: row.config.command ?? '',
    args: row.config.args ?? [],
    timeout_seconds: row.config.timeout_seconds ?? 15,
    enabled: row.enabled,
  })
  argsText.value = (row.config.args ?? []).join(' ')
  dialogVisible.value = true
}

function buildPayload(): MCPServerPayload {
  return {
    ...form,
    args: form.transport === 'stdio' ? argsText.value.split(' ').filter(Boolean) : [],
  }
}

async function submit() {
  if (!form.name) {
    ElMessage.warning('名称必填')
    return
  }
  if (form.transport !== 'stdio' && !form.endpoint) {
    ElMessage.warning('远程 MCP 服务需填写 Endpoint')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      await apiUpdateMcpServer(editingId.value, buildPayload())
      ElMessage.success('已更新')
    } else {
      await apiCreateMcpServer(buildPayload())
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

async function onToggle(row: MCPServer) {
  try {
    await apiUpdateMcpServer(row.id, { enabled: row.enabled })
  } catch (e: any) {
    row.enabled = !row.enabled
    ElMessage.error(e?.message || '操作失败')
  }
}

async function test(row: MCPServer) {
  const loadingMsg = ElMessage({ message: '测试连接中…', duration: 0 })
  try {
    const payload: MCPServerPayload = {
      name: row.name,
      transport: row.config.transport,
      endpoint: row.config.endpoint,
      command: row.config.command,
      args: row.config.args ?? [],
      timeout_seconds: row.config.timeout_seconds ?? 15,
    }
    const res = await apiTestMcpServer(payload)
    loadingMsg.close()
    if (res.ok) {
      ElMessage.success(`连接成功，发现 ${res.tool_count} 个工具`)
    } else {
      ElMessage.error(`连接失败：${res.error || '未知错误'}`)
    }
  } catch (e: any) {
    loadingMsg.close()
    ElMessage.error(e?.message || '测试失败')
  }
}

function openTools(row: MCPServer) {
  toolsServerId.value = row.id
  toolsServerName.value = row.name
  tools.value = []
  toolsError.value = ''
  toolsDialogVisible.value = true
  loadTools(row.id)
}

async function loadTools(id: string) {
  if (!id) return
  toolsLoading.value = true
  toolsError.value = ''
  try {
    const res = await apiListMcpTools(id)
    if (res.ok) {
      tools.value = res.tools ?? []
    } else {
      tools.value = []
      toolsError.value = res.error || '无法连接该 MCP 服务'
    }
  } catch (e: any) {
    tools.value = []
    toolsError.value = e?.message || '获取工具列表失败'
  } finally {
    toolsLoading.value = false
  }
}

async function remove(row: MCPServer) {
  try {
    await ElMessageBox.confirm(`确认删除 MCP 服务「${row.name}」？`, '删除确认', { type: 'warning' })
    await apiDeleteMcpServer(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.muted {
  color: #909399;
}
.tools-summary {
  margin-bottom: 8px;
  color: #606266;
  font-size: 13px;
}
.schema-pre {
  margin: 0;
  padding: 8px 12px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
