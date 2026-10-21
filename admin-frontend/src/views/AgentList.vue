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
          <el-tag v-if="row.stale" type="danger" size="small">已失效</el-tag>
          <el-tag v-else :type="row.source === 'code' ? 'info' : 'success'" size="small">
            {{ row.source === 'code' ? '代码' : '动态' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" show-overflow-tooltip />
      <el-table-column label="归属场景" width="140">
        <template #default="{ row }">
          <el-tooltip
            v-if="row.scene_id"
            placement="top"
            content="动态 Agent 只在创建它的场景内可被绑定；代码 Agent 为全局可用"
          >
            <el-tag size="small" type="success">{{ sceneName(row.scene_id) }}</el-tag>
          </el-tooltip>
          <span v-else class="muted">全局</span>
        </template>
      </el-table-column>
      <el-table-column width="200">
        <template #header>
          知识库
          <el-tooltip placement="top" content="标签依次为：主知识库、附加知识库；开启全部知识库时显示「全部库」。悬停各标签无额外说明，具体含义见编辑弹窗的问号提示。">
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <template #default="{ row }">
          <el-tag v-if="row.rag_namespace" size="small" style="margin-right: 4px">{{ row.rag_namespace }}</el-tag>
          <el-tag
            v-for="ns in row.rag_namespaces"
            :key="ns"
            size="small"
            type="info"
            style="margin-right: 4px"
          >{{ ns }}</el-tag>
          <span
            v-if="row.search_all_knowledge_bases"
            class="muted"
          >全部库</span>
          <span
            v-if="!row.rag_namespace && (!row.rag_namespaces || !row.rag_namespaces.length) && !row.search_all_knowledge_bases"
            class="muted"
          >—</span>
        </template>
      </el-table-column>
      <el-table-column label="MCP 工具" width="200">
        <template #default="{ row }">
          <el-tag v-for="t in row.mcp_tools" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
          <span v-if="!row.mcp_tools || !row.mcp_tools.length" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="自定义工具" width="200">
        <template #default="{ row }">
          <el-tag v-for="t in row.custom_tool_names" :key="t" size="small" type="success" style="margin-right: 4px">{{ t }}</el-tag>
          <span v-if="!row.custom_tool_names || !row.custom_tool_names.length" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }">
          <el-switch v-if="!row.stale" v-model="row.enabled" @change="toggle(row)" />
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="210" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
          <template v-if="row.source === 'dynamic'">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
          <el-button v-else-if="row.stale" link type="danger" size="small" @click="remove(row)">删除</el-button>
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
      <el-form-item>
        <template #label>
          路由关键词
          <el-tooltip
            placement="top"
            content="告诉路由分发器“什么样的客户提问该派给我”。输入一个词后回车添加，可添多个；代码 Agent 的关键词在 Python 类里声明，此处仅维护动态 Agent。"
          >
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <el-input-tag
          v-model="form.routing_keywords"
          :max="20"
          :token-separators="[',', '，']"
          clearable
          placeholder="输入关键词后回车添加，如：发票、开票、税号（逗号也可分隔）"
          style="width: 100%"
        />
        <div class="muted hint">建议 5~12 个，写客户口语说法（“快递到哪了”）而非内部术语；不填则路由只能依描述猜测适用范围。实际注入了哪几条，可在「路由分发器」页查看</div>
      </el-form-item>
      <el-form-item>
        <template #label>
          典型问法
          <el-tooltip
            placement="top"
            content="写 2~3 句客户真实会说的话，帮助模型建立语义映射：关键词管字面命中，典型问法管语义贴合。"
          >
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <el-input-tag
          v-model="form.routing_examples"
          :max="5"
          clearable
          placeholder="输入一句客户问法后回车添加，如：帮我开张发票"
          style="width: 100%"
        />
        <div class="muted hint">仅前 3 条会注入路由提示词，建议写 2~3 条即可</div>
      </el-form-item>
      <el-form-item label="模型">
        <el-input v-model="form.llm_model" placeholder="留空使用默认模型（LLM_MODEL_ID）" />
      </el-form-item>
      <el-form-item>
        <template #label>
          主知识库
          <el-tooltip placement="top" content="模型主动调用 RAG 工具时默认检索的库，并在跨库检索中排在最前。">
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <el-select v-model="form.rag_namespace" clearable placeholder="可选，本 Agent 归属的主知识库">
          <el-option v-for="kb in knowledgeBases" :key="kb.namespace" :label="kb.display_name" :value="kb.namespace" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <template #label>
          附加知识库
          <el-tooltip
            placement="top"
            content="对话时会自动一并检索的其他库（可与主库组成多个库）。注意：模型主动调用 RAG 工具仍只检索主知识库，附加库通过检索上下文自动注入。"
          >
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <el-select
          v-model="form.rag_namespaces"
          multiple
          clearable
          :disabled="form.search_all_knowledge_bases"
          placeholder="可选，额外跨库检索（与主库合并去重）"
        >
          <el-option
            v-for="kb in knowledgeBases"
            :key="kb.namespace"
            :label="kb.display_name"
            :value="kb.namespace"
          />
        </el-select>
        <div v-if="form.search_all_knowledge_bases" class="muted hint">已开启“全部知识库”，此处选择被忽略</div>
      </el-form-item>
      <el-form-item>
        <template #label>
          全部知识库
          <el-tooltip
            placement="top"
            content="开启后覆盖全部启用中的知识库（含后台新建的库，无需改代码），并忽略上方主知识库 / 附加知识库的单独选择。"
          >
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
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
      <el-form-item>
        <template #label>
          自定义工具
          <el-tooltip
            placement="top"
            content="代码开发并在「自定义工具」页登记启用后可绑定；仅动态 Agent 支持。实现来自代码，后台不可编辑其逻辑。"
          >
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <el-select v-model="form.custom_tool_names" multiple clearable placeholder="选择已登记的自定义工具">
          <el-option
            v-for="t in registeredCustomTools"
            :key="t.name"
            :label="t.display_description || t.name"
            :value="t.name"
            :disabled="!t.enabled"
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

  <el-dialog v-model="detailVisible" title="Agent 详情" width="720px">
    <div v-loading="detailLoading">
      <el-descriptions v-if="detail" :column="2" border size="small">
        <el-descriptions-item label="名称">{{ detail.name }}</el-descriptions-item>
        <el-descriptions-item label="显示名">{{ detail.display_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="来源">
          <el-tag :type="detail.source === 'code' ? 'info' : 'success'" size="small">
            {{ detail.source === 'code' ? '代码' : '动态' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="启用">
          <el-tag :type="detail.enabled ? 'success' : 'danger'" size="small">{{ detail.enabled ? '启用' : '禁用' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="归属场景">
          <el-tag v-if="detail.scene_id" size="small" type="success">{{ sceneName(detail.scene_id) }}</el-tag>
          <span v-else class="muted">全局（所有场景可绑定）</span>
        </el-descriptions-item>
        <el-descriptions-item label="模型">{{ detail.model || '—' }}</el-descriptions-item>
        <el-descriptions-item label="知识库">
          <el-tag v-if="detail.search_all_knowledge_bases" size="small" style="margin-right: 4px">全部库</el-tag>
          <el-tag v-if="detail.rag_namespace" size="small" style="margin-right: 4px">{{ detail.rag_namespace }}</el-tag>
          <el-tag
            v-for="ns in detail.rag_namespaces"
            :key="ns"
            size="small"
            type="info"
            style="margin-right: 4px"
          >{{ ns }}</el-tag>
          <span
            v-if="!detail.search_all_knowledge_bases && !detail.rag_namespace && (!detail.rag_namespaces || !detail.rag_namespaces.length)"
            class="muted"
          >—</span>
        </el-descriptions-item>
        <el-descriptions-item label="描述" :span="2">{{ detail.description || '—' }}</el-descriptions-item>
        <el-descriptions-item label="路由关键词" :span="2">
          <el-tag v-for="k in detail.routing_keywords" :key="k" size="small" style="margin-right: 4px">{{ k }}</el-tag>
          <span v-if="!detail.routing_keywords || !detail.routing_keywords.length" class="muted">—（未配置，路由仅依描述判定）</span>
        </el-descriptions-item>
        <el-descriptions-item label="典型问法" :span="2">
          <div v-for="e in detail.routing_examples" :key="e">{{ e }}</div>
          <span v-if="!detail.routing_examples || !detail.routing_examples.length" class="muted">—</span>
        </el-descriptions-item>
        <el-descriptions-item label="MCP 工具" :span="2">
          <el-tag v-for="t in detail.mcp_tools" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
          <span v-if="!detail.mcp_tools || !detail.mcp_tools.length" class="muted">—</span>
        </el-descriptions-item>
        <el-descriptions-item label="自定义工具" :span="2">
          <el-tag v-for="t in detail.custom_tool_names" :key="t" size="small" type="success" style="margin-right: 4px">{{ t }}</el-tag>
          <span v-if="!detail.custom_tool_names || !detail.custom_tool_names.length" class="muted">—</span>
        </el-descriptions-item>
        <el-descriptions-item v-if="detail.registered_tools" label="注册工具" :span="2">
          <el-tag v-for="t in detail.registered_tools" :key="t" size="small" type="info" style="margin-right: 4px">{{ t }}</el-tag>
          <span v-if="!detail.registered_tools.length" class="muted">—</span>
        </el-descriptions-item>
        <el-descriptions-item label="System Prompt" :span="2">
          <pre v-if="detail.system_prompt" class="prompt-pre">{{ detail.system_prompt }}</pre>
          <span v-else class="muted">—</span>
        </el-descriptions-item>
      </el-descriptions>
    </div>
    <template #footer>
      <el-button @click="detailVisible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiCreateAgent,
  apiDeleteAgent,
  apiGetAgentDetail,
  apiListAgents,
  apiListBases,
  apiListCustomTools,
  apiListMcpServers,
  apiListScenes,
  apiToggleAgent,
  apiUpdateAgent,
} from '@/api'
import type { AgentInfo, CustomToolSpec, DynamicAgentPayload, KnowledgeBase, MCPServer, Scene } from '@/types'

const agents = ref<AgentInfo[]>([])
const mcpServers = ref<MCPServer[]>([])
const knowledgeBases = ref<KnowledgeBase[]>([])
const customTools = ref<CustomToolSpec[]>([])
const scenes = ref<Scene[]>([])
const loading = ref(false)

/** 场景 id → 展示名：列表与详情共用，避免逐行查询 */
function sceneName(sceneId: string): string {
  return scenes.value.find((s) => s.id === sceneId)?.display_name || '未知场景'
}

// 仅已登记的自定义工具可作为绑定候选（未登记表示代码有但未开放给 Agent）
const registeredCustomTools = computed(() => customTools.value.filter((t) => t.registered))

const dialogVisible = ref(false)
const submitting = ref(false)
const editing = ref(false)

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<AgentInfo | null>(null)

const emptyForm = (): DynamicAgentPayload => ({
  name: '',
  display_name: '',
  description: '',
  routing_keywords: [],
  routing_examples: [],
  system_prompt: '',
  llm_model: '',
  rag_namespace: null,
  rag_namespaces: [],
  search_all_knowledge_bases: false,
  mcp_tool_names: [],
  custom_tool_names: [],
  enabled: true,
})
const form = reactive<DynamicAgentPayload>(emptyForm())

async function load() {
  loading.value = true
  try {
    const [a, m, k, ct, sc] = await Promise.all([
      apiListAgents(),
      apiListMcpServers(),
      apiListBases(),
      apiListCustomTools(),
      apiListScenes(),
    ])
    agents.value = a
    mcpServers.value = m
    knowledgeBases.value = k
    customTools.value = ct
    scenes.value = sc
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

async function openDetail(row: AgentInfo) {
  detail.value = row
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await apiGetAgentDetail(row.name)
  } catch (e: any) {
    ElMessage.error(e?.message || '获取详情失败，已展示列表数据')
  } finally {
    detailLoading.value = false
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
    routing_keywords: [...(row.routing_keywords ?? [])],
    routing_examples: [...(row.routing_examples ?? [])],
    system_prompt: row.system_prompt ?? '',
    llm_model: row.model ?? '',
    rag_namespace: row.rag_namespace ?? null,
    rag_namespaces: [...(row.rag_namespaces ?? [])],
    search_all_knowledge_bases: row.search_all_knowledge_bases ?? false,
    mcp_tool_names: [...(row.mcp_tools ?? [])],
    custom_tool_names: [...(row.custom_tool_names ?? [])],
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
    const tip = row.stale
      ? `该 Agent 对应代码已下线，确认删除残留记录「${row.name}」？`
      : `确认删除动态 Agent「${row.name}」？`
    await ElMessageBox.confirm(tip, '删除确认', { type: 'warning' })
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
.hint {
  font-size: 12px;
  line-height: 1.4;
}
.q-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  margin-left: 4px;
  border-radius: 50%;
  background: #909399;
  color: #fff;
  font-size: 10px;
  line-height: 1;
  cursor: help;
  vertical-align: middle;
}
.prompt-pre {
  margin: 0;
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.5;
  background: #f5f7fa;
  border-radius: 4px;
  padding: 8px 12px;
}
</style>
