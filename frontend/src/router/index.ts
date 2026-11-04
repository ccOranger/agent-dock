import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { getToken } from '@/api/http'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/chat' },
  { path: '/login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
  { path: '/chat', component: () => import('@/views/customer/ChatView.vue') },
  { path: '/history', component: () => import('@/views/customer/HistoryView.vue') },
  // 管理功能（知识库/Agent/记忆/仪表盘/咨询管理）已迁移至独立后台工程 admin-frontend（:5174）
]

const router = createRouter({
  // 与 Vite base 保持一致：部署到 /smart-cs/ 时自动使用 /smart-cs/ 作为路由前缀
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

router.beforeEach((to) => {
  if (to.meta.public) {
    // 已登录还去登录页：直接回聊天首页，避免重复登录
    if (to.path === '/login' && getToken()) return '/chat'
    return true
  }
  // 未登录一律先去登录页，并记住原目标（登录后跳回来，而不是永远落在 /chat）
  if (!getToken()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
