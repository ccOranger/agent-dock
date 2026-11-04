<template>
  <el-container class="admin-layout">
    <el-aside :width="collapsed ? '64px' : '220px'" class="aside">
      <div class="brand" :class="{ 'is-collapsed': collapsed }">
        <span class="brand-mark">AD</span>
        <span v-if="!collapsed" class="brand-text">AgentDock 管理后台</span>
      </div>

      <el-scrollbar class="menu-scroll">
        <el-menu
          class="menu"
          :collapse="collapsed"
          :collapse-transition="false"
          :default-active="activePath"
          :default-openeds="defaultOpeneds"
          router
        >
          <!-- 单页入口（人工接管，带待接入角标） -->
          <el-menu-item v-for="s in visibleStandalone" :key="s.key" :index="s.path">
            <el-icon><component :is="s.icon" /></el-icon>
            <template #title>
              <span class="menu-label">{{ s.title }}</span>
              <span v-if="s.badge && pendingTakeover > 0" class="takeover-count">
                {{ pendingTakeover > 99 ? '99+' : pendingTakeover }}
              </span>
            </template>
          </el-menu-item>

          <!-- 分组：按职责折叠，减少顶层条目 -->
          <el-sub-menu v-for="g in visibleGroups" :key="g.key" :index="g.key">
            <template #title>
              <el-icon><component :is="g.icon" /></el-icon>
              <span class="menu-label">{{ g.title }}</span>
            </template>
            <el-menu-item v-for="c in g.children" :key="c.path" :index="c.path">
              <el-icon><component :is="c.icon" /></el-icon>
              <template #title>{{ c.title }}</template>
            </el-menu-item>
          </el-sub-menu>
        </el-menu>
      </el-scrollbar>
    </el-aside>

    <el-container class="main-wrap">
      <el-header class="header">
        <div class="header-left">
          <el-button text class="collapse-btn" :title="collapsed ? '展开菜单' : '收起菜单'" @click="toggleCollapse">
            <el-icon :size="18"><component :is="collapsed ? Expand : Fold" /></el-icon>
          </el-button>
          <el-breadcrumb separator="/" class="crumb">
            <el-breadcrumb-item v-if="currentGroupTitle">{{ currentGroupTitle }}</el-breadcrumb-item>
            <el-breadcrumb-item>{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="user">
          <el-tag size="small" type="info" effect="plain">{{ auth.user?.role }}</el-tag>
          <span class="name">{{ auth.user?.display_name || auth.user?.username }}</span>
          <el-button
            text
            class="density-btn"
            :title="density === 'compact' ? '表格密度：紧凑（点击切换为舒适）' : '表格密度：舒适（点击切换为紧凑）'"
            aria-label="切换表格密度"
            @click="toggleDensity"
          >
            <el-icon :size="16"><Operation /></el-icon>
          </el-button>
          <el-button size="small" text @click="onLogout">退出</el-button>
        </div>
      </el-header>

      <el-main class="main">
        <div class="page-container">
          <router-view />
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ChatDotRound,
  ChatLineSquare,
  Collection,
  Connection,
  Cpu,
  DataLine,
  Expand,
  Fold,
  Grid,
  Lock,
  Memo,
  Odometer,
  Operation,
  Service,
  Setting,
  Share,
  Switch,
  Tickets,
  Tools,
  TrendCharts,
  User,
} from '@element-plus/icons-vue'
import { apiListTakeovers } from '@/api'
import { canAccess, useAuthStore } from '@/stores/auth'
import { useDensity } from '@/composables/useDensity'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const { density, toggle: toggleDensity } = useDensity()

const activePath = computed(() => route.path)

// 侧栏折叠状态持久化：小屏/宽表场景下用户希望自己决定
const COLLAPSE_KEY = 'cs-admin:collapsed'
const collapsed = ref(localStorage.getItem(COLLAPSE_KEY) === '1')

function toggleCollapse() {
  collapsed.value = !collapsed.value
  localStorage.setItem(COLLAPSE_KEY, collapsed.value ? '1' : '0')
}

// ===== 菜单分组：按职责折叠、按 RBAC 权限过滤（perm = 菜单模块 key）=====
interface MenuItem {
  path: string
  title: string
  perm: string
  icon: Component
}
interface MenuEntry {
  key: string
  title: string
  icon: Component
  path?: string
  badge?: boolean
  perm?: string
  children?: MenuItem[]
}

const MENU: MenuEntry[] = [
  { key: 'takeover', path: '/takeover', title: '人工接管', badge: true, perm: 'takeover', icon: Service },
  {
    key: 'service',
    title: '咨询与工单',
    icon: ChatDotRound,
    children: [
      { path: '/inquiries', title: '咨询管理', perm: 'inquiries', icon: ChatDotRound },
      { path: '/tickets', title: '工单管理', perm: 'tickets', icon: Tickets },
    ],
  },
  {
    key: 'content',
    title: '知识与记忆',
    icon: Collection,
    children: [
      { path: '/knowledge', title: '知识库维护', perm: 'knowledge', icon: Collection },
      { path: '/canned-scripts', title: '固定话术', perm: 'canned-scripts', icon: ChatLineSquare },
      { path: '/memory', title: '记忆管理', perm: 'memory', icon: Memo },
    ],
  },
  {
    key: 'observe',
    title: '监控与分析',
    icon: DataLine,
    children: [
      { path: '/traces', title: '链路追踪', perm: 'traces', icon: Share },
      { path: '/dashboard', title: '仪表盘', perm: 'dashboard', icon: Odometer },
      { path: '/analytics', title: '数据分析', perm: 'analytics', icon: TrendCharts },
    ],
  },
  {
    key: 'scene',
    title: '场景与 Agent',
    icon: Grid,
    children: [
      { path: '/scenes', title: '业务场景', perm: 'scenes', icon: Grid },
      { path: '/agents', title: 'Agent 管理', perm: 'agents', icon: Cpu },
      { path: '/router', title: '路由分发器', perm: 'router', icon: Switch },
    ],
  },
  {
    key: 'tools',
    title: '工具集成',
    icon: Tools,
    children: [
      { path: '/mcp-servers', title: 'MCP 服务管理', perm: 'mcp-servers', icon: Connection },
      { path: '/custom-tools', title: '自定义工具', perm: 'custom-tools', icon: Tools },
    ],
  },
  {
    key: 'system',
    title: '系统管理',
    icon: Setting,
    children: [
      { path: '/staff', title: '人员与配额', perm: 'staff', icon: User },
      { path: '/roles', title: '角色与权限', perm: 'roles', icon: Lock },
    ],
  },
]

// 可见性统一走 RBAC：拥有该模块的 read 权限即可见（admin 恒为 true）
const visible = (perm?: string) => (perm ? canAccess(auth.user, perm, 'read') : false)

// 单页入口（带角标）：按权限显隐
const visibleStandalone = computed<MenuEntry[]>(() => {
  const out: MenuEntry[] = []
  for (const e of MENU) {
    if (e.path && visible(e.perm)) out.push(e)
  }
  return out
})

// 分组：子项全不可见的分组整体隐藏
const visibleGroups = computed(() =>
  MENU.filter((e) => !e.path && (e.children || []).some((c) => visible(c.perm))).map((e) => ({
    ...e,
    children: (e.children || []).filter((c) => visible(c.perm)),
  })),
)

// 初始展开当前路由所在分组（深链时能看到高亮项的父级）
const defaultOpeneds = computed(() => {
  const p = route.path
  for (const e of MENU) {
    if (e.path) continue
    if ((e.children || []).some((c) => p === c.path || p.startsWith(c.path + '/'))) return [e.key]
  }
  return []
})

// 面包屑：分组名 / 页面名
const currentTitle = computed(() => (route.meta.title as string) || 'AgentDock 管理后台')
const currentGroupTitle = computed(() => {
  const p = route.path
  for (const e of MENU) {
    if (e.path) continue
    if ((e.children || []).some((c) => p === c.path || p.startsWith(c.path + '/'))) return e.title
  }
  return ''
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

// 待接手会话数：非接管权限账号调用会 403，失败不影响后台其它操作
onMounted(() => {
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

/* ---------- 侧栏 ---------- */
.aside {
  background: var(--cs-sidebar-bg);
  border-right: 1px solid var(--cs-sidebar-border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.2s ease;
}

.brand {
  height: var(--cs-header-h);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  border-bottom: 1px solid var(--cs-sidebar-border);
  flex-shrink: 0;
}

.brand.is-collapsed {
  justify-content: center;
  padding: 0;
}

.brand-mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--cs-radius-sm);
  background: linear-gradient(135deg, var(--cs-brand), var(--cs-brand-strong));
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
}

.brand-text {
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
}

.menu-scroll {
  flex: 1;
  min-height: 0;
}

.menu {
  border-right: none;
  padding: 10px;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--cs-sidebar-text);
  --el-menu-active-color: var(--cs-sidebar-text-active);
  --el-menu-hover-bg-color: var(--cs-sidebar-hover);
  --el-menu-item-height: 42px;
  --el-menu-sub-item-height: 40px;
}

.menu :deep(.el-menu-item),
.menu :deep(.el-sub-menu__title) {
  border-radius: var(--cs-radius-sm);
  margin-bottom: 2px;
  color: var(--cs-sidebar-text);
}

.menu :deep(.el-sub-menu .el-menu) {
  background: transparent;
}

.menu :deep(.el-menu-item:hover),
.menu :deep(.el-sub-menu__title:hover) {
  background: var(--cs-sidebar-hover);
  color: #fff;
}

/* 选中态：品牌色实底（Ant Pro 深色菜单口径），不再用左侧强调条 */
.menu :deep(.el-menu-item.is-active) {
  background: var(--cs-sidebar-active-bg);
  color: var(--cs-sidebar-text-active);
  font-weight: 500;
  box-shadow: none;
}

.menu :deep(.el-sub-menu.is-active > .el-sub-menu__title) {
  color: #fff;
}

/* 折叠成图标栏：图标居中，去掉左右内边距 */
.menu.el-menu--collapse {
  padding: 10px 0;
}

.menu.el-menu--collapse :deep(.el-menu-item),
.menu.el-menu--collapse :deep(.el-sub-menu__title) {
  padding: 0 !important;
  margin: 0 8px 2px;
  justify-content: center;
}

.menu-label {
  flex: 1;
}

.takeover-count {
  display: inline-block;
  min-width: 18px;
  height: 18px;
  line-height: 18px;
  padding: 0 5px;
  box-sizing: border-box;
  border-radius: 9px;
  background: var(--cs-danger);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  text-align: center;
  vertical-align: middle;
}

/* ---------- 顶栏 ---------- */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--cs-header-h);
  padding: 0 16px 0 8px;
  background: var(--cs-surface);
  border-bottom: 1px solid var(--cs-border);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.collapse-btn {
  color: var(--cs-text-2);
  padding: 6px;
}

.density-btn {
  color: var(--cs-text-2);
  padding: 6px;
}

.crumb :deep(.el-breadcrumb__inner) {
  color: var(--cs-text-3);
  font-weight: 400;
}

.crumb :deep(.el-breadcrumb__item:last-child .el-breadcrumb__inner) {
  color: var(--cs-text-1);
  font-weight: 600;
}

.user {
  display: flex;
  align-items: center;
  gap: 10px;
}

.name {
  color: var(--cs-text-2);
  font-size: 13px;
}

/* ---------- 内容区 ---------- */
.main {
  padding: 0;
  background: var(--cs-bg);
  overflow: auto;
}

.main-wrap {
  min-width: 0;
}

.page-container {
  padding: 20px 24px 28px;
  max-width: 1680px;
  margin: 0 auto;
}
</style>
