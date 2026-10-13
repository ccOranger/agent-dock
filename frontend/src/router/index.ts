import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { getToken } from '@/api/http'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/chat' },
  { path: '/login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
  { path: '/chat', component: () => import('@/views/customer/ChatView.vue') },
  { path: '/history', component: () => import('@/views/customer/HistoryView.vue') },
  {
    path: '/admin/knowledge',
    component: () => import('@/views/knowledge/DocumentList.vue'),
    meta: { roles: ['admin', 'agent'] },
  },
  {
    path: '/admin/memory/:userId?',
    component: () => import('@/views/memory/MemoryView.vue'),
    meta: { roles: ['agent', 'admin'] },
  },
  {
    path: '/admin/agents',
    component: () => import('@/views/admin/AgentList.vue'),
    meta: { roles: ['admin'] },
  },
  {
    path: '/admin/dashboard',
    component: () => import('@/views/admin/Dashboard.vue'),
    meta: { roles: ['admin'] },
  },
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
