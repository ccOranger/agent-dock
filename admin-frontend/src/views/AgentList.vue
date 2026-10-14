<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <b>Agent 管理</b>
        <el-button type="primary" size="small" @click="openCreate">新增 Agent</el-button>
      </div>
    </template>

    <el-table :data="agents" v-loading="loading">
      <el-table-column prop="name" label="名称" width="160" />
      <el-table-column label="来源" width="90">
        <template #default="{ row }">
          <el-tag :type="row.source === 'code' ? 'info' : 'success'" size="small">
            {{ row.source === 'code' ? '代码' : '动态' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" show-overflow-tooltip />
      <el-table-column prop="rag_namespace" label="Namespace" width="140" />
      <el-table-column label="MCP 工具" width="200">
        <template #default="{ row }">
          <el-tag v-for="t in row.mcp_tools" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
          <span v-if="!row.mcp_tools || !row.mcp_tools.length" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="toggle(row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <template v-if="row.source === 'dynamic'">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
          <span v-else class="muted">只读</span>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-dialog v-model="dialogVisible" :title="editing ? '编辑 Agent' : '新增 Agent'" width="640px">
    <el-form :model="form" label-width="120px">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" :disabled="editing" placeholder="唯一标识，字母/数字/下划线" />
      </el-form-item>
      <el-form-item label="显示名">
        <el-input v-model="form.display_name" placeholder="可选" />
      </el-form-item>
      <el-form-item label="描述" required>
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          placeholder="供路由识别用途，请写清适用场景，如：退款咨询处理"
        />
      </el-form-item>
      <el-form-item label="System Prompt" required>
        <el-input v-model="form.system_prompt" type="textarea" :rows="5" placeholder="动态 Agent 的核心提示词" />
      </el-form-item>
      <el-form-item label="模型">
        <el-input v-model="form.llm_model" placeholder="留空使用默认模型（LLM_MODEL_ID）" />
      </el-form-item>
      <el-form-item label="知识库 Namespace">
        <el-select v-model="form.rag_namespace" clearable placeholder="可选，绑定单个知识库">
          <el-option v-for="kb in knowledgeBases" :key="kb.namespace" :label="kb.display_name" :value="kb.namespace" />
        </el-select>
      </el-form-item>
      <el-form-item label="检索全部知识库">
        <el-switch v-model="form.search_all_knowledge_bases" />
      </el-form-item>
      <el-form-item label="MCP 工具">
        <el-select v-model="form.mcp_tool_names" multiple clearable placeholder="选择已配置的 MCP 服务">
          <el-option
            v-for="mcp in mcpServers"
            :key="mcp.name"
            :label="mcp.name"
            :value="mcp.name"
            :disabled="!mcp.enabled"
          />
        </el-select>
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
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiCreateAgent,
  apiDeleteAgent,
  apiListAgents,
  apiListBases,
  apiListMcpServers,
  apiToggleAgent,
  apiUpdateAgent,
} from '@/api'
import type { AgentInfo, DynamicAgentPayload, KnowledgeBase, MCPServer } from '@/types'

const agents = ref<AgentInfo[]>([])
const mcpServers = ref<MCPServer[]>([])
const knowledgeBases = ref<KnowledgeBase[]>([])
const loading = ref(false)

const dialogVisible = ref(false)
const submitting = ref(false)
const editing = ref(false)

const emptyForm = (): DynamicAgentPayload => ({
  name: '',
  display_name: '',
  description: '',
  system_prompt: '',
  llm_model: '',
  rag_namespace: null,
  search_all_knowledge_bases: false,
  mcp_tool_names: [],
  enabled: true,
})
const form = reactive<DynamicAgentPayload>(emptyForm())

async function load() {
  loading.value = true
  try {
    const [a, m, k] = await Promise.all([apiListAgents(), apiListMcpServers(), apiListBases()])
    agents.value = a
    mcpServers.value = m
    knowledgeBases.value = k
  } finally {
    loading.value = false
  }
}

async function toggle(row: AgentInfo) {
  try {
    await apiToggleAgent(row.name, row.enabled)
    ElMessage.success(`${row.name} 已${row.enabled ? '启用' : '禁用'}`)
  } catch (e: any) {
    row.enabled = !row.enabled
    ElMessage.error(e?.message || '操作失败')
  }
}

function openCreate() {
  editing.value = false
  Object.assign(form, emptyForm())
  dialogVisible.value = true
}

function openEdit(row: AgentInfo) {
  editing.value = true
  Object.assign(form, emptyForm(), {
    name: row.name,
    display_name: row.display_name ?? '',
    description: row.description,
    system_prompt: row.system_prompt ?? '',
    llm_model: row.model ?? '',
    rag_namespace: row.rag_namespace ?? null,
    search_all_knowledge_bases: row.search_all_knowledge_bases ?? false,
    mcp_tool_names: [...(row.mcp_tools ?? [])],
    enabled: row.enabled,
  })
  dialogVisible.value = true
}

async function submit() {
  if (!form.name || !form.description || !form.system_prompt) {
    ElMessage.warning('名称、描述、System Prompt 均为必填')
    return
  }
  submitting.value = true
  try {
    if (editing.value) {
      const { name, ...rest } = form
      await apiUpdateAgent(name, rest)
      ElMessage.success('已更新')
    } else {
      await apiCreateAgent({ ...form })
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

async function remove(row: AgentInfo) {
  try {
    await ElMessageBox.confirm(`确认删除动态 Agent「${row.name}」？`, '删除确认', { type: 'warning' })
    await apiDeleteAgent(row.name)
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
</style>
