import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { getToken } from '@/api/http'
import { canAccess, useAuthStore } from '@/stores/auth'

// meta.perm = 对应可授权菜单模块 key（与后端 core/rbac 对齐）；菜单显隐与守卫都按它判定。
// 未配 perm 的（如 integrations）仍按 meta.roles 粗粒度限制。
const children: RouteRecordRaw[] = [
  { path: '', redirect: () => firstAllowed() },
  { path: 'takeover', component: () => import('@/views/TakeoverView.vue'), meta: { title: '人工接管', perm: 'takeover' } },
  { path: 'inquiries', component: () => import('@/views/InquiriesView.vue'), meta: { title: '咨询管理', perm: 'inquiries' } },
  { path: 'tickets', component: () => import('@/views/TicketsView.vue'), meta: { title: '工单管理', perm: 'tickets' } },
  { path: 'traces', component: () => import('@/views/TracesView.vue'), meta: { title: '链路追踪', perm: 'traces' } },
  { path: 'knowledge', component: () => import('@/views/KnowledgeBaseView.vue'), meta: { title: '知识库维护', perm: 'knowledge' } },
  { path: 'canned-scripts', component: () => import('@/views/CannedScriptsView.vue'), meta: { title: '固定话术', perm: 'canned-scripts' } },
  { path: 'memory/:userId?', component: () => import('@/views/MemoryView.vue'), meta: { title: '记忆管理', perm: 'memory' } },
  { path: 'dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '仪表盘', perm: 'dashboard' } },
  { path: 'analytics', component: () => import('@/views/AnalyticsView.vue'), meta: { title: '数据分析', perm: 'analytics' } },
  { path: 'scenes', component: () => import('@/views/ScenesView.vue'), meta: { title: '业务场景', perm: 'scenes' } },
  { path: 'agents', component: () => import('@/views/AgentList.vue'), meta: { title: 'Agent 管理', perm: 'agents' } },
  { path: 'router', component: () => import('@/views/RouterInfoView.vue'), meta: { title: '路由分发器', perm: 'router' } },
  { path: 'mcp-servers', component: () => import('@/views/McpServersView.vue'), meta: { title: 'MCP 服务管理', perm: 'mcp-servers' } },
  { path: 'custom-tools', component: () => import('@/views/CustomToolsView.vue'), meta: { title: '自定义工具', perm: 'custom-tools' } },
  { path: 'integrations', component: () => import('@/views/IntegrationsView.vue'), meta: { title: '接入方管理', roles: ['admin'] } },
  { path: 'staff', component: () => import('@/views/StaffQuotaView.vue'), meta: { title: '人员与配额', perm: 'staff' } },
  { path: 'roles', component: () => import('@/views/RoleView.vue'), meta: { title: '角色与权限', perm: 'roles' } },
  // 兜底 404：未知路径仍留在后台外壳里，避免只渲染出侧栏和一片空白
  { path: ':pathMatch(.*)*', component: () => import('@/views/NotFoundView.vue'), meta: { title: '页面不存在' } },
]

const routes: RouteRecordRaw[] = [
  { path: '/login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('@/layouts/AdminLayout.vue'),
    children,
  },
]

const router = createRouter({
  // 开发为 /，生产镜像挂在 /admin/（由 Vite base 注入）
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

/** 落地页：返回当前用户第一个有权查看的模块，兜底用咨询管理 */
function firstAllowed(): string {
  const auth = useAuthStore()
  for (const c of children) {
    const perm = (c.meta as { perm?: string })?.perm
    if (perm && canAccess(auth.user, perm, 'read')) return '/' + c.path.replace(/\/:.*$/, '')
  }
  return '/inquiries'
}

// ===== 发版后的 chunk 失效兜底 =====
// 旧标签页里的懒加载路由仍指向上一版的文件名，部署后点击会动态导入失败并白屏。
// 这里捕获一次并自动刷新；用 sessionStorage 标记防止「刷新后仍然失败」变成死循环。
const RELOAD_FLAG = 'cs-admin:chunk-reload'

router.onError((err) => {
  const message = String((err as Error)?.message || '')
  if (!/dynamically imported module|Importing a module script failed/i.test(message)) return
  if (sessionStorage.getItem(RELOAD_FLAG)) return
  sessionStorage.setItem(RELOAD_FLAG, '1')
  location.reload()
})

router.afterEach(() => {
  // 导航成功后清掉标记，下一次发版仍可自动刷新
  if (sessionStorage.getItem(RELOAD_FLAG)) {
    setTimeout(() => sessionStorage.removeItem(RELOAD_FLAG), 8000)
  }
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  if (!getToken()) return '/login'
  const auth = useAuthStore()
  if (!auth.user) {
    try {
      await auth.fetchMe()
    } catch {
      return '/login'
    }
  }
  // 粗粒度角色限制（未纳入 RBAC 的模块，如接入方管理）
  const roles = to.meta.roles as string[] | undefined
  if (roles && auth.user && !roles.includes(auth.user.role)) {
    return firstAllowed()
  }
  // RBAC：按菜单模块可见性放行；无权限则转到第一个可看的模块
  const perm = to.meta.perm as string | undefined
  if (perm && auth.user && !canAccess(auth.user, perm, 'read')) {
    const fallback = firstAllowed()
    // 兜底目标自己也无权限时（如空授权账号）直接放行，由页面处理 403，避免死循环
    return fallback === to.path ? true : fallback
  }
  return true
})

export default router
