<template>
  <div class="page">
    <PageHeader
      title="客户记忆查看"
      description="按客户 UUID 查登录客户的记忆，嵌入渠道的匿名访客用 visitor:<UUID>；可从咨询列表点「记忆」直接带参跳转。"
      :count="queried && filtered.length ? `${filtered.length} 条` : undefined"
    >
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <el-card>
      <FilterBar :loading="loading" @search="load" @reset="reset">
        <el-input
          v-model="subject"
          placeholder="客户 UUID，或访客 visitor:<UUID>"
          clearable
          style="width: 380px"
          @keyup.enter="load"
        />
        <el-select v-model="type" placeholder="记忆类型" clearable style="width: 150px" @change="load">
          <el-option v-for="(label, val) in MEMORY_TYPE_LABELS" :key="val" :label="label" :value="val" />
        </el-select>
        <el-input v-model="keyword" placeholder="在记忆内容中搜索" clearable style="width: 200px" />
      </FilterBar>

      <el-alert
        v-if="error"
        type="error"
        :closable="false"
        show-icon
        :title="error"
        class="err-tip"
      />

      <div class="result" v-loading="loading">
        <el-empty
          v-if="!queried"
          description="输入客户 UUID 后点「查询」；匿名的嵌入访客请用 visitor:<UUID>"
        />
        <el-empty
          v-else-if="!filtered.length"
          :description="keyword ? '没有包含该关键词的记忆，可清空搜索词' : '该客户暂无记忆'"
        />
        <MemoryTimeline v-else :items="filtered" />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiUserMemories } from '@/api'
import { MEMORY_TYPE_LABELS, type MemoryItem } from '@/types'
import MemoryTimeline from '@/components/MemoryTimeline.vue'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'

const route = useRoute()
const subject = ref((route.params.userId as string) || '')
const type = ref<string>()
const keyword = ref('')
const items = ref<MemoryItem[]>([])
const loading = ref(false)
const queried = ref(false)
const error = ref('')

const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

/** 归一化客户主体：裸 UUID 原样返回，访客补 visitor: 前缀；非法返回空串 */
function normalizeSubject(raw: string): string {
  const value = raw.trim()
  if (!value) return ''
  if (value.startsWith('visitor:')) {
    const id = value.slice(8)
    return UUID_RE.test(id) ? `visitor:${id}` : ''
  }
  return UUID_RE.test(value) ? value : ''
}

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return items.value
  return items.value.filter((m) => (m.content || '').toLowerCase().includes(kw))
})

async function load() {
  const subjectValue = normalizeSubject(subject.value)
  if (!subjectValue) {
    ElMessage.warning('请输入客户 UUID，或访客用 visitor:<UUID>')
    return
  }
  subject.value = subjectValue
  loading.value = true
  error.value = ''
  try {
    items.value = await apiUserMemories(subjectValue, type.value)
    queried.value = true
  } catch (e: any) {
    items.value = []
    queried.value = true
    error.value = e?.message || '加载记忆失败'
  } finally {
    loading.value = false
  }
}

function reset() {
  subject.value = ''
  type.value = undefined
  keyword.value = ''
  items.value = []
  queried.value = false
  error.value = ''
}

onMounted(() => {
  // 从路由带参（咨询列表「记忆」按钮）时自动查询
  if (subject.value) load()
})
</script>

<style scoped>
.err-tip {
  margin-top: 12px;
}

.result {
  margin-top: 16px;
  min-height: 80px;
}
</style>
