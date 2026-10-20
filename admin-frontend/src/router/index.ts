import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { getToken } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  { path: '/login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('@/layouts/AdminLayout.vue'),
    children: [
      { path: '', redirect: '/inquiries' },
      { path: 'inquiries', component: () => import('@/views/InquiriesView.vue'), meta: { title: '咨询管理' } },
      { path: 'tickets', component: () => import('@/views/TicketsView.vue'), meta: { title: '工单管理' } },
      { path: 'traces', component: () => import('@/views/TracesView.vue'), meta: { title: '链路追踪' } },
      { path: 'knowledge', component: () => import('@/views/KnowledgeBaseView.vue'), meta: { title: '知识库维护' } },
      { path: 'packs', component: () => import('@/views/PacksView.vue'), meta: { title: '场景包', roles: ['admin'] } },
      { path: 'agents', component: () => import('@/views/AgentList.vue'), meta: { title: 'Agent 管理', roles: ['admin'] } },
      { path: 'router', component: () => import('@/views/RouterInfoView.vue'), meta: { title: '路由分发器', roles: ['admin'] } },
      { path: 'mcp-servers', component: () => import('@/views/McpServersView.vue'), meta: { title: 'MCP 服务管理', roles: ['admin'] } },
      { path: 'custom-tools', component: () => import('@/views/CustomToolsView.vue'), meta: { title: '自定义工具', roles: ['admin'] } },
      { path: 'canned-scripts', component: () => import('@/views/CannedScriptsView.vue'), meta: { title: '固定话术', roles: ['admin'] } },
      { path: 'memory/:userId?', component: () => import('@/views/MemoryView.vue'), meta: { title: '记忆管理', roles: ['agent', 'admin'] } },
      { path: 'dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '仪表盘', roles: ['admin'] } },
    ],
  },
]

const router = createRouter({
  // 开发为 /，生产镜像挂在 /admin/（由 Vite base 注入）
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
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
  const roles = to.meta.roles as string[] | undefined
  if (roles && auth.user && !roles.includes(auth.user.role)) {
    return '/inquiries'
  }
  return true
})

export default router
