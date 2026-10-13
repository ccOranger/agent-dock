<template>
  <div class="history-page">
    <el-card>
      <template #header>
        <div class="head">
          <b>历史会话</b>
          <el-button size="small" @click="router.push('/chat')">返回对话</el-button>
        </div>
      </template>
      <el-table :data="sessions" v-loading="loading">
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column prop="last_route_to" label="最近路由" width="150" />
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ dayjs(row.created_at).format('YYYY-MM-DD HH:mm') }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button size="small" @click="viewMessages(row.id)">查看</el-button>
            <el-button size="small" type="danger" plain @click="remove(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        style="margin-top: 12px"
        layout="prev, pager, next"
        :total="total"
        :page-size="20"
        v-model:current-page="page"
        @current-change="load"
      />
    </el-card>

    <el-dialog v-model="dialogVisible" title="历史消息" width="640px">
      <div class="msgs">
        <MessageBubble v-for="m in messages" :key="m.id" :message="m" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessageBox } from 'element-plus'
import { apiDeleteSession, apiListSessions, apiSessionMessages } from '@/api'
import type { Message, SessionInfo } from '@/types'
import MessageBubble from '@/components/MessageBubble.vue'

const router = useRouter()
const sessions = ref<SessionInfo[]>([])
const messages = ref<Message[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const dialogVisible = ref(false)

async function load() {
  loading.value = true
  try {
    const data = await apiListSessions(page.value, 20)
    sessions.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function viewMessages(id: string) {
  const data = await apiSessionMessages(id, 1, 100)
  messages.value = data.items
  dialogVisible.value = true
}

async function remove(id: string) {
  await ElMessageBox.confirm('确认删除该会话？', '提示', { type: 'warning' })
  await apiDeleteSession(id)
  load()
}

onMounted(load)
</script>

<style scoped>
.history-page {
  padding: 24px 8%;
}
.head {
  display: flex;
  justify-content: space-between;
}
.msgs {
  max-height: 60vh;
  overflow-y: auto;
}
</style>
