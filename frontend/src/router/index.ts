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
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!getToken()) return '/login'
  return true
})

export default router
