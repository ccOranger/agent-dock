<template>
  <el-container class="admin-layout">
    <el-aside width="220px">
      <div class="brand">客服管理后台</div>
      <el-menu :default-active="activePath" router class="menu">
        <el-menu-item index="/inquiries">
          <span>咨询管理</span>
        </el-menu-item>
        <el-menu-item index="/knowledge">
          <span>知识库维护</span>
        </el-menu-item>
        <el-menu-item index="/agents" v-if="isAdmin">
          <span>Agent 管理</span>
        </el-menu-item>
        <el-menu-item index="/mcp-servers" v-if="isAdmin">
          <span>MCP 服务管理</span>
        </el-menu-item>
        <el-menu-item index="/memory">
          <span>记忆管理</span>
        </el-menu-item>
        <el-menu-item index="/dashboard" v-if="isAdmin">
          <span>仪表盘</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div class="title">{{ route.meta.title || '智能客服管理后台' }}</div>
        <div class="user">
          <el-tag size="small" type="info">{{ auth.user?.role }}</el-tag>
          <span class="name">{{ auth.user?.display_name || auth.user?.username }}</span>
          <el-button size="small" text @click="onLogout">退出</el-button>
        </div>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const activePath = computed(() => route.path)
const isAdmin = computed(() => auth.user?.role === 'admin')

function onLogout() {
  auth.logout()
  ElMessage.success('已退出')
  router.push('/login')
}
</script>

<style scoped>
.admin-layout {
  height: 100vh;
}
.el-aside {
  background: #1f2d3d;
  color: #fff;
}
.brand {
  height: 60px;
  line-height: 60px;
  text-align: center;
  font-weight: 700;
  color: #fff;
  border-bottom: 1px solid #2c3e50;
}
.menu {
  border-right: none;
  background: #1f2d3d;
}
.menu :deep(.el-menu-item) {
  color: #cfd8e3;
}
.menu :deep(.el-menu-item.is-active) {
  color: #fff;
  background: #2b5aa0;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #ebeef5;
}
.title {
  font-weight: 600;
}
.user {
  display: flex;
  align-items: center;
  gap: 10px;
}
.name {
  color: #606266;
}
.el-main {
  background: #f5f7fa;
}
</style>
