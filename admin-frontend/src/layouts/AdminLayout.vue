<template>
  <el-container class="admin-layout">
    <el-aside width="220px">
      <div class="brand">AgentDock 管理后台</div>
      <el-menu :default-active="activePath" :default-openeds="defaultOpeneds" router class="menu">
        <!-- 单页入口（人工接管，带待接入角标） -->
        <el-menu-item v-for="s in visibleStandalone" :key="s.key" :index="s.path">
          <span>{{ s.title }}</span>
          <span v-if="s.badge && pendingTakeover > 0" class="takeover-count">
            {{ pendingTakeover > 99 ? '99+' : pendingTakeover }}
          </span>
        </el-menu-item>
        <!-- 分组：按职责折叠，减少顶层条目 -->
        <el-sub-menu v-for="g in visibleGroups" :key="g.key" :index="g.key">
          <template #title><span>{{ g.title }}</span></template>
          <el-menu-item v-for="c in g.children" :key="c.path" :index="c.path">
            {{ c.title }}
          </el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div class="title">{{ route.meta.title || 'AgentDock 管理后台' }}</div>
        <div class="user">
          <el-tag size="small" type="info">{{ auth.user?.role }}</el-tag>
          <span class="name">{{ auth.user?.display_name || auth.user?.username }}</span>
          <el-button size="small" text @click="onLogout">退出</el-button>
        </div>
      </el-header>
      <div v-if="license.banner" class="banner">
        <el-alert :type="license.banner.type" :title="license.banner.text" show-icon :closable="false" />
        <el-link type="primary" :underline="false" @click="router.push('/license')">查看授权</el-link>
      </div>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiListTakeovers } from '@/api'
import { canAccess, useAuthStore } from '@/stores/auth'
import { useLicenseStore } from '@/stores/license'

const auth = useAuthStore()
const license = useLicenseStore()
const route = useRoute()
const router = useRouter()

const activePath = computed(() => route.path)
const isAdmin = computed(() => auth.user?.role === 'admin')

// ===== 菜单分组：按职责折叠、按 RBAC 权限过滤（perm = 菜单模块 key）=====
type MenuItem = { path: string; title: string; perm: string }
interface MenuEntry {
  key: string
  title: string
  path?: string
  badge?: boolean
  perm?: string
  children?: MenuItem[]
}
interface StandaloneItem {
  key: string
  path: string
  title: string
  badge?: boolean
}
interface GroupItem {
  key: string
  title: string
  children: MenuItem[]
}

const MENU: MenuEntry[] = [
  { key: 'takeover', path: '/takeover', title: '人工接管', badge: true, perm: 'takeover' },
  {
    key: 'service',
    title: '咨询与工单',
    children: [
      { path: '/inquiries', title: '咨询管理', perm: 'inquiries' },
      { path: '/tickets', title: '工单管理', perm: 'tickets' },
    ],
  },
  {
    key: 'content',
    title: '知识与记忆',
    children: [
      { path: '/knowledge', title: '知识库维护', perm: 'knowledge' },
      { path: '/canned-scripts', title: '固定话术', perm: 'canned-scripts' },
      { path: '/memory', title: '记忆管理', perm: 'memory' },
    ],
  },
  {
    key: 'observe',
    title: '监控与分析',
    children: [
      { path: '/traces', title: '链路追踪', perm: 'traces' },
      { path: '/dashboard', title: '仪表盘', perm: 'dashboard' },
      { path: '/usage', title: '用量报表', perm: 'usage' },
    ],
  },
  {
    key: 'scene',
    title: '场景与 Agent',
    children: [
      { path: '/scenes', title: '业务场景', perm: 'scenes' },
      { path: '/agents', title: 'Agent 管理', perm: 'agents' },
      { path: '/router', title: '路由分发器', perm: 'router' },
    ],
  },
  {
    key: 'tools',
    title: '工具集成',
    children: [
      { path: '/mcp-servers', title: 'MCP 服务管理', perm: 'mcp-servers' },
      { path: '/custom-tools', title: '自定义工具', perm: 'custom-tools' },
    ],
  },
  {
    key: 'system',
    title: '系统管理',
    children: [
      { path: '/staff', title: '人员与配额', perm: 'staff' },
      { path: '/roles', title: '角色与权限', perm: 'roles' },
      { path: '/license', title: '授权与版本', perm: 'license' },
    ],
  },
]

// 可见性统一走 RBAC：拥有该模块的 read 权限即可见（admin 恒为 true）
const visible = (perm?: string) => (perm ? canAccess(auth.user, perm, 'read') : false)

// 单页入口（带角标）：按权限显隐
const visibleStandalone = computed<StandaloneItem[]>(() => {
  const out: StandaloneItem[] = []
  for (const e of MENU) {
    if (e.path && visible(e.perm)) out.push({ key: e.key, path: e.path, title: e.title, badge: e.badge })
  }
  return out
})

// 分组：子项全不可见的分组整体隐藏
const visibleGroups = computed<GroupItem[]>(() => {
  const out: GroupItem[] = []
  for (const e of MENU) {
    if (e.path) continue
    const children = (e.children || []).filter((c) => visible(c.perm))
    if (children.length) out.push({ key: e.key, title: e.title, children })
  }
  return out
})

// 初始展开当前路由所在分组（深链时能看到高亮项的父级）
const defaultOpeneds = computed(() => {
  const p = route.path
  for (const e of MENU) {
    if (e.path) continue
    if ((e.children || []).some((c) => p === c.path || p.startsWith(c.path + '/'))) return [e.key]
  }
  return []
})

// 待接入角标：坐席/管理员都轮询排队数（status=queued），提醒有新会话等待接入
const pendingTakeover = ref(0)
let pollTimer: ReturnType<typeof setInterval> | null = null
async function refreshPending() {
  try {
    const data = await apiListTakeovers({ status: 'queued', page: 1, page_size: 1 })
    pendingTakeover.value = data.total
  } catch {
    /* 无权限或网络异常不影响后台其它操作 */
  }
}

// 授权状态只有 admin 能查（agent 调用 403）；降级时顶部常驻提醒，不阻断任何操作
onMounted(() => {
  if (isAdmin.value) license.load()
  refreshPending()
  pollTimer = setInterval(refreshPending, 20000)
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
})

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
  display: flex;
  flex-direction: column;
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
  flex: 1;
  overflow-y: auto;
}
.menu :deep(.el-menu-item) {
  color: #cfd8e3;
}
.menu :deep(.el-menu-item.is-active) {
  color: #fff;
  background: #2b5aa0;
}
.menu :deep(.el-sub-menu .el-menu) {
  background: #1a2634;
}
.menu :deep(.el-sub-menu__title) {
  color: #cfd8e3;
}
.menu :deep(.el-sub-menu .el-menu-item:hover),
.menu :deep(.el-sub-menu__title:hover) {
  background: #2c3e50;
}
.takeover-count {
  margin-left: 8px;
  display: inline-block;
  min-width: 18px;
  height: 18px;
  line-height: 18px;
  padding: 0 5px;
  box-sizing: border-box;
  border-radius: 9px;
  background: #f56c6c;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  text-align: center;
  vertical-align: middle;
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
.banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 20px 0;
}
.banner :deep(.el-alert) {
  flex: 1;
}
.el-main {
  background: #f5f7fa;
}
</style>
