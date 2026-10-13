<template>
  <div class="page">
    <el-card>
      <template #header>
        <div class="head">
          <b>知识库管理</b>
          <el-select v-model="namespace" style="width: 180px" @change="load">
            <el-option label="cs_default（通用）" value="cs_default" />
            <el-option label="cs_order（订单）" value="cs_order" />
            <el-option label="cs_after_sales（售后）" value="cs_after_sales" />
          </el-select>
        </div>
      </template>

      <DocumentUploader :namespace="namespace" @uploaded="load" />

      <el-table :data="docs" v-loading="loading" style="margin-top: 16px">
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="rag_namespace" label="Namespace" width="150" />
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="分块数" width="90" />
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" @click="reindex(row.id)">重建索引</el-button>
            <el-button size="small" type="danger" plain @click="remove(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card style="margin-top: 16px">
      <template #header><b>检索测试</b></template>
      <div class="search-bar">
        <el-input v-model="query" placeholder="输入检索问题" style="max-width: 400px" />
        <el-button type="primary" @click="search">检索</el-button>
      </div>
      <pre v-if="searchResult" class="result">{{ searchResult }}</pre>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiDeleteDocument, apiListDocuments, apiRagSearch } from '@/api'
import { http } from '@/api/http'
import type { DocumentItem } from '@/types'
import DocumentUploader from '@/components/DocumentUploader.vue'

const namespace = ref('cs_default')
const docs = ref<DocumentItem[]>([])
const loading = ref(false)
const query = ref('')
const searchResult = ref('')

function statusType(s: string) {
  return ({ ready: 'success', failed: 'danger', processing: 'warning' } as any)[s] ?? 'info'
}

async function load() {
  loading.value = true
  try {
    const data = await apiListDocuments(namespace.value)
    docs.value = data.items
  } finally {
    loading.value = false
  }
}

async function reindex(id: string) {
  await http.post(`/knowledge/documents/${id}/reindex`)
  ElMessage.success('已触发重建索引')
  load()
}

async function remove(id: string) {
  await ElMessageBox.confirm('确认删除该文档？', '提示', { type: 'warning' })
  await apiDeleteDocument(id)
  load()
}

async function search() {
  if (!query.value.trim()) return
  const data = await apiRagSearch(namespace.value, query.value)
  searchResult.value = JSON.stringify(data.results, null, 2)
}

onMounted(load)
</script>

<style scoped>
.page {
  padding: 24px 8%;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.search-bar {
  display: flex;
  gap: 8px;
}
.result {
  margin-top: 12px;
  background: #f6f8fa;
  padding: 12px;
  max-height: 300px;
  overflow: auto;
}
</style>
