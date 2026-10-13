<template>
  <div class="page">
    <el-card>
      <template #header>
        <div class="head">
          <b>记忆查看</b>
          <el-radio-group v-model="scope" size="small">
            <el-radio-button value="me">我的记忆</el-radio-button>
            <el-radio-button value="user" v-if="auth.user?.role !== 'customer'">指定用户</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <div class="filters">
        <el-input
          v-if="scope === 'user'"
          v-model="userId"
          placeholder="用户 ID"
          style="width: 280px"
        />
        <el-select v-model="type" placeholder="记忆类型" clearable style="width: 160px">
          <el-option label="working" value="working" />
          <el-option label="episodic" value="episodic" />
          <el-option label="semantic" value="semantic" />
          <el-option label="perceptual" value="perceptual" />
        </el-select>
        <el-button type="primary" @click="load">查询</el-button>
      </div>
      <div style="margin-top: 16px" v-loading="loading">
        <MemoryTimeline :items="items" />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { apiMyMemories, apiUserMemories } from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { MemoryItem } from '@/types'
import MemoryTimeline from '@/components/MemoryTimeline.vue'

const auth = useAuthStore()
const route = useRoute()
const scope = ref<'me' | 'user'>('me')
const userId = ref((route.params.userId as string) || '')
const type = ref<string>()
const items = ref<MemoryItem[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    items.value =
      scope.value === 'me'
        ? await apiMyMemories(type.value)
        : await apiUserMemories(userId.value, type.value)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (userId.value && auth.user?.role !== 'customer') scope.value = 'user'
  load()
})
</script>

<style scoped>
.page {
  padding: 24px 8%;
}
.head {
  display: flex;
  justify-content: space-between;
}
.filters {
  display: flex;
  gap: 8px;
}
</style>
