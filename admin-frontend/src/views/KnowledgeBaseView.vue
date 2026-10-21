<template>
  <div>
    <!-- 知识库命名空间 -->
    <el-card>
      <template #header>
        <div class="head">
          <b>知识库（命名空间）</b>
          <el-button type="primary" plain @click="openBaseCreate">新建知识库</el-button>
        </div>
      </template>
      <el-table :data="bases" v-loading="basesLoading">
        <el-table-column prop="namespace" label="Namespace" width="180" />
        <el-table-column prop="display_name" label="名称" width="160" />
        <el-table-column label="归属场景" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.scene_code" size="small" type="success">
              {{ sceneName(row.scene_code) }}
            </el-tag>
            <el-tooltip v-else content="平台共享库：需在 Agent 的 RAG 命名空间里显式引用才会检索" placement="top">
              <el-tag size="small" type="info">平台共享</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="180" />
        <el-table-column prop="document_count" label="文档数" width="90" />
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch :model-value="row.is_active" @change="(v: boolean) => toggleActive(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button size="small" :type="row.namespace === ns ? 'primary' : 'default'" plain @click="selectNs(row.namespace)">
              查看文档
            </el-button>
            <el-button size="small" @click="openBaseEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" plain @click="removeBase(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 当前知识库：文档 + 条目 + 检索 -->
    <el-card style="margin-top: 16px" v-if="ns">
      <template #header>
        <div class="head">
          <b>文档 / 条目 · {{ ns }}</b>
          <div>
            <el-button type="primary" plain @click="openEntryCreate" v-if="isAdmin">+ 手工条目</el-button>
          </div>
        </div>
      </template>

      <DocumentUploader v-if="isAdmin" :namespace="ns" @uploaded="loadDocs" />

      <div class="filters" style="margin-top: 12px">
        <el-select v-model="docStatus" placeholder="状态" clearable style="width: 130px" @change="loadDocs">
          <el-option label="pending" value="pending" />
          <el-option label="processing" value="processing" />
          <el-option label="ready" value="ready" />
          <el-option label="failed" value="failed" />
        </el-select>
        <el-select v-model="docKind" placeholder="类型" clearable style="width: 130px" @change="loadDocs">
          <el-option label="上传文档" value="upload" />
          <el-option label="手工条目" value="manual" />
        </el-select>
      </div>

      <el-table :data="docs" v-loading="docsLoading" style="margin-top: 12px">
        <el-table-column prop="title" label="标题" min-width="180" />
        <el-table-column prop="kind" label="类型" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.kind === 'manual' ? 'warning' : 'info'">
              {{ row.kind === 'manual' ? '手工条目' : '文档' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="140">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
            <el-tooltip v-if="row.error_message" :content="row.error_message" placement="top">
              <el-text type="danger" size="small" style="margin-left: 4px">原因</el-text>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="分块数" width="90" />
        <el-table-column prop="created_at" label="创建时间" width="200">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="300">
          <template #default="{ row }">
            <el-button size="small" @click="viewChunks(row)">分块</el-button>
            <el-tooltip :disabled="row.has_archive" content="历史文档无原文，请重新上传" placement="top">
              <span>
                <el-button size="small" :disabled="!row.has_archive" @click="downloadRaw(row)">原文</el-button>
              </span>
            </el-tooltip>
            <el-button size="small" v-if="row.kind === 'manual' && isAdmin" @click="openEntryEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" plain v-if="isAdmin" @click="removeDoc(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 检索测试 -->
    <el-card style="margin-top: 16px" v-if="ns">
      <template #header><b>检索测试</b></template>
      <div class="filters">
        <el-input v-model="query" placeholder="输入检索问题" style="max-width: 360px" @keyup.enter="search" />
        <el-input-number v-model="topK" :min="1" :max="20" />
        <el-input-number v-model="minScore" :min="0" :max="1" :step="0.05" :precision="2" controls-position="right" style="width: 110px" />
        <el-tooltip content="高级检索使用 HyDE+MQE（依赖大模型）；关闭后为纯向量检索" placement="top">
          <span><el-switch v-model="advanced" active-text="高级检索" /></span>
        </el-tooltip>
        <el-button type="primary" :loading="searching" @click="search">检索</el-button>
      </div>
      <div v-if="searched" class="search-results">
        <el-empty v-if="!searchResults.length" description="未找到相关内容（可尝试关闭高级检索或调低相似度阈值）" />
        <div v-for="(r, i) in searchResults" :key="i" class="search-item">
          <div class="search-item-head">
            <el-tag size="small" :type="scoreType(r.score)">相似度 {{ (r.score ?? 0).toFixed(3) }}</el-tag>
            <span class="search-src" :title="r.source || ''">{{ r.source_name || '未知来源' }}</span>
            <span v-if="r.heading_path" class="search-heading">{{ r.heading_path }}</span>
          </div>
          <div class="search-content">{{ r.content }}</div>
        </div>
      </div>
    </el-card>

    <!-- 新建/编辑知识库 -->
    <el-dialog v-model="baseDialog" :title="baseEditing ? '编辑知识库' : '新建知识库'" width="460px">
      <el-form :model="baseForm" label-width="90px">
        <el-form-item label="Namespace">
          <el-input v-model="baseForm.namespace" :disabled="baseEditing" placeholder="仅字母数字下划线" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="baseForm.display_name" />
        </el-form-item>
        <el-form-item label="归属场景">
          <el-select v-model="baseForm.scene_code" :disabled="baseEditing" clearable placeholder="平台共享" style="width: 100%">
            <el-option label="平台共享（不自动检索）" value="" />
            <el-option
              v-for="scene in scenes"
              :key="scene.code"
              :label="scene.display_name"
              :value="scene.code"
            />
          </el-select>
          <div class="form-tip">
            归属场景后，该场景内开启「检索全部知识库」的 Agent 会自动覆盖这个库；平台共享库只在 Agent 显式写了命名空间时才会被检索。归属创建后可在场景弹窗内调整。
          </div>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="baseForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="baseDialog = false">取消</el-button>
        <el-button type="primary" @click="submitBase">保存</el-button>
      </template>
    </el-dialog>

    <!-- 手工条目 -->
    <el-dialog v-model="entryDialog" :title="entryEditing ? '编辑手工条目' : '新建手工条目'" width="600px">
      <el-form :model="entryForm" label-width="60px">
        <el-form-item label="标题">
          <el-input v-model="entryForm.title" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="entryForm.content" type="textarea" :rows="8" placeholder="FAQ / 知识内容（支持 Markdown）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="entryDialog = false">取消</el-button>
        <el-button type="primary" @click="submitEntry">保存并入库</el-button>
      </template>
    </el-dialog>

    <!-- 分块查看 -->
    <el-dialog v-model="chunkDialog" title="已索引分块" width="700px">
      <div v-loading="chunkLoading" class="chunks">
        <div v-for="c in chunks" :key="c.id" class="chunk">
          <div class="chunk-meta">#{{ c.chunk_index ?? '-' }} · {{ c.id }}</div>
          <div class="chunk-content">{{ c.content }}</div>
        </div>
        <el-empty v-if="!chunks.length && !chunkLoading" description="暂无分块（该文档向量可能已被清理）" />
        <div style="text-align: center; margin-top: 8px">
          <el-button v-if="nextOffset" size="small" @click="loadMoreChunks">加载更多</el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import {
  apiCreateBase,
  apiCreateEntry,
  apiDeleteBase,
  apiDeleteDocument,
  apiDocChunks,
  apiDocRaw,
  apiListBases,
  apiListDocuments,
  apiRagSearch,
  apiSceneOptions,
  apiUpdateBase,
  apiUpdateEntry,
} from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { Chunk, DocumentItem, KnowledgeBase, SceneOption, SearchResult } from '@/types'
import DocumentUploader from '@/components/DocumentUploader.vue'

const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')

function fmt(v?: string | null) {
  return v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'
}

// ---- 命名空间 ----
const bases = ref<KnowledgeBase[]>([])
const basesLoading = ref(false)
const ns = ref<string>('')
const scenes = ref<SceneOption[]>([])

function sceneName(code?: string | null) {
  if (!code) return ''
  return scenes.value.find((s) => s.code === code)?.display_name || code
}

async function loadBases() {
  basesLoading.value = true
  try {
    bases.value = await apiListBases()
    if (!ns.value && bases.value.length) ns.value = bases.value[0].namespace
  } finally {
    basesLoading.value = false
  }
}

function selectNs(namespace: string) {
  ns.value = namespace
  loadDocs()
}

const baseDialog = ref(false)
const baseEditing = ref(false)
const baseForm = reactive({ namespace: '', display_name: '', description: '', scene_code: '' })

function openBaseCreate() {
  baseEditing.value = false
  Object.assign(baseForm, { namespace: '', display_name: '', description: '', scene_code: '' })
  baseDialog.value = true
}
function openBaseEdit(row: KnowledgeBase) {
  baseEditing.value = true
  Object.assign(baseForm, {
    namespace: row.namespace,
    display_name: row.display_name,
    description: row.description || '',
    scene_code: row.scene_code || '',
  })
  baseDialog.value = true
}
async function submitBase() {
  try {
    if (baseEditing.value) {
      await apiUpdateBase(baseForm.namespace, { display_name: baseForm.display_name, description: baseForm.description })
    } else {
      await apiCreateBase({
        namespace: baseForm.namespace,
        display_name: baseForm.display_name,
        description: baseForm.description,
        scene_code: baseForm.scene_code || null,
      })
    }
    ElMessage.success('已保存')
    baseDialog.value = false
    loadBases()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}
async function toggleActive(row: KnowledgeBase, v: boolean) {
  try {
    await apiUpdateBase(row.namespace, { is_active: v })
    row.is_active = v
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}
async function removeBase(row: KnowledgeBase) {
  await ElMessageBox.confirm(`确认删除知识库 ${row.namespace}？`, '提示', { type: 'warning' })
  try {
    await apiDeleteBase(row.namespace)
    ElMessage.success('已删除')
    if (ns.value === row.namespace) ns.value = ''
    loadBases()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail?.message || e?.message || '删除失败')
  }
}

// ---- 文档 ----
const docs = ref<DocumentItem[]>([])
const docsLoading = ref(false)
const docStatus = ref<string>()
const docKind = ref<string>()

async function loadDocs() {
  if (!ns.value) return
  docsLoading.value = true
  try {
    const data = await apiListDocuments(ns.value, 1, 100, { status: docStatus.value, kind: docKind.value })
    docs.value = data.items
  } finally {
    docsLoading.value = false
  }
}
function statusType(s: string) {
  return ({ ready: 'success', failed: 'danger', processing: 'warning' } as any)[s] ?? 'info'
}
async function removeDoc(row: DocumentItem) {
  await ElMessageBox.confirm(`确认删除「${row.title}」？将同时清理其向量。`, '提示', { type: 'warning' })
  try {
    await apiDeleteDocument(row.id)
    ElMessage.success('已删除')
    loadDocs()
    loadBases()
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

// ---- 分块查看 ----
const chunkDialog = ref(false)
const chunkLoading = ref(false)
const chunks = ref<Chunk[]>([])
const nextOffset = ref<string | null>(null)
const chunkDocId = ref('')

async function viewChunks(row: DocumentItem) {
  chunkDocId.value = row.id
  chunks.value = []
  nextOffset.value = null
  chunkDialog.value = true
  await fetchChunks(false)
}
async function fetchChunks(append: boolean) {
  chunkLoading.value = true
  try {
    const data = await apiDocChunks(chunkDocId.value, 50, append ? nextOffset.value || undefined : undefined)
    chunks.value = append ? [...chunks.value, ...data.chunks] : data.chunks
    nextOffset.value = data.next_offset
  } catch (e: any) {
    ElMessage.error(e?.message || '加载分块失败')
  } finally {
    chunkLoading.value = false
  }
}
function loadMoreChunks() {
  fetchChunks(true)
}

// ---- 原文下载 ----
async function downloadRaw(row: DocumentItem) {
  try {
    const blob = await apiDocRaw(row.id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = row.original_filename || row.title || `document-${row.id}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    ElMessage.error('下载原文失败：' + (e?.message || '未知错误'))
  }
}

// ---- 手工条目 ----
const entryDialog = ref(false)
const entryEditing = ref(false)
const entryForm = reactive({ id: '', title: '', content: '' })

function openEntryCreate() {
  entryEditing.value = false
  Object.assign(entryForm, { id: '', title: '', content: '' })
  entryDialog.value = true
}
function openEntryEdit(row: DocumentItem) {
  entryEditing.value = true
  Object.assign(entryForm, { id: row.id, title: row.title, content: '' })
  entryDialog.value = true
  ElMessage.info('编辑需重新填写完整内容以重建索引')
}
async function submitEntry() {
  try {
    if (entryEditing.value) {
      await apiUpdateEntry(entryForm.id, { title: entryForm.title, content: entryForm.content })
    } else {
      await apiCreateEntry({ namespace: ns.value, title: entryForm.title, content: entryForm.content })
    }
    ElMessage.success('已保存并入库')
    entryDialog.value = false
    loadDocs()
    loadBases()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}

// ---- 检索测试 ----
const query = ref('')
const topK = ref(5)
const minScore = ref(0.3)
const advanced = ref(true)
const searching = ref(false)
const searched = ref(false)
const searchResults = ref<SearchResult[]>([])
async function search() {
  if (!query.value.trim()) return
  searching.value = true
  try {
    searchResults.value = await apiRagSearch(ns.value, query.value, topK.value, {
      advanced: advanced.value,
      min_score: minScore.value,
    })
    searched.value = true
  } catch (e: any) {
    ElMessage.error(e?.message || '检索失败')
  } finally {
    searching.value = false
  }
}
function scoreType(s: number) {
  return s >= 0.7 ? 'success' : s >= 0.5 ? 'warning' : 'info'
}

onMounted(async () => {
  apiSceneOptions()
    .then((list) => (scenes.value = list))
    .catch(() => {
      /* 场景下拉失败不影响知识库管理 */
    })
  await loadBases()
  if (ns.value) loadDocs()
})
</script>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.form-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
  margin-top: 4px;
}
.result {
  margin-top: 12px;
  background: #f6f8fa;
  padding: 12px;
  max-height: 320px;
  overflow: auto;
}
.chunks {
  max-height: 60vh;
  overflow: auto;
}
.chunk {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
}
.chunk-meta {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
.chunk-content {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  color: #303133;
}
.search-results {
  margin-top: 12px;
}
.search-item {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.search-item-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.search-src {
  font-size: 13px;
  color: #409eff;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}
.search-heading {
  font-size: 12px;
  color: #909399;
}
.search-content {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
  max-height: 200px;
  overflow: auto;
}
</style>
