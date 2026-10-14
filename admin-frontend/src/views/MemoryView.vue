<template>
  <el-card>
    <template #header>
      <b>客户记忆查看</b>
    </template>
    <div class="filters">
      <el-input
        v-model="userId"
        placeholder="客户 ID（可从咨询列表复制）"
        clearable
        style="width: 340px"
        @keyup.enter="load"
      />
      <el-select v-model="type" placeholder="记忆类型" clearable style="width: 160px">
        <el-option label="working" value="working" />
        <el-option label="episodic" value="episodic" />
        <el-option label="semantic" value="semantic" />
        <el-option label="perceptual" value="perceptual" />
      </el-select>
      <el-button type="primary" :disabled="!userId" @click="load">查询</el-button>
    </div>
    <el-empty
      v-if="!userId && !loading"
      description="请输入客户 ID 查询该客户的记忆"
      style="margin-top: 24px"
    />
    <div v-else style="margin-top: 16px" v-loading="loading">
      <MemoryTimeline :items="items" />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiUserMemories } from '@/api'
import type { MemoryItem } from '@/types'
import MemoryTimeline from '@/components/MemoryTimeline.vue'

const route = useRoute()
const userId = ref((route.params.userId as string) || '')
const type = ref<string>()
const items = ref<MemoryItem[]>([])
const loading = ref(false)

async function load() {
  if (!userId.value) {
    ElMessage.warning('请先输入客户 ID')
    return
  }
  loading.value = true
  try {
    items.value = await apiUserMemories(userId.value, type.value)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  // 从路由带参（如从咨询列表跳转）时自动查询
  if (userId.value) load()
})
</script>

<style scoped>
.filters {
  display: flex;
  gap: 8px;
}
</style>
