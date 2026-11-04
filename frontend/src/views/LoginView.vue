<template>
  <div class="login-page">
    <el-card class="login-card">
      <h2>AgentDock</h2>
      <el-tabs v-model="mode">
        <el-tab-pane label="登录" name="login">
          <el-form :model="form" @submit.prevent="onLogin">
            <el-form-item>
              <el-input v-model="form.username" placeholder="用户名" />
            </el-form-item>
            <el-form-item>
              <el-input v-model="form.password" type="password" placeholder="密码" show-password />
            </el-form-item>
            <el-button type="primary" style="width: 100%" :loading="loading" @click="onLogin">
              登录
            </el-button>
            <p class="hint">还没有账号？切换到「注册」创建客户账号</p>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="注册" name="register">
          <el-form :model="form" @submit.prevent="onRegister">
            <el-form-item>
              <el-input v-model="form.username" placeholder="用户名（至少 2 位）" />
            </el-form-item>
            <el-form-item>
              <el-input
                v-model="form.password"
                type="password"
                placeholder="密码（至少 6 位）"
                show-password
              />
            </el-form-item>
            <el-button type="primary" style="width: 100%" :loading="loading" @click="onRegister">
              注册并登录
            </el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const mode = ref<'login' | 'register'>('login')
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

/**
 * 登录后的落点：优先回到被拦截前的页面（?redirect=），
 * 只接受站内绝对路径，避免被拼成外链。
 */
function afterLoginPath(): string {
  const raw = route.query.redirect
  const target = typeof raw === 'string' ? raw : ''
  return target.startsWith('/') && !target.startsWith('//') ? target : '/chat'
}

async function onLogin() {
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    router.push(afterLoginPath())
  } catch (e: any) {
    // code 2004：账号不存在，引导切换到注册并保留已输入的用户名
    if (e?.code === 2004) {
      ElMessage.warning('账号不存在，已为你切换到注册')
      mode.value = 'register'
    } else {
      ElMessage.error(e?.message || '登录失败')
    }
  } finally {
    loading.value = false
  }
}

async function onRegister() {
  loading.value = true
  try {
    await auth.register(form.username, form.password)
    ElMessage.success('注册成功，已自动登录')
    router.push(afterLoginPath())
  } catch (e: any) {
    ElMessage.error(e?.message || '注册失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  padding: 20px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background:
    radial-gradient(720px 420px at 8% 0%, rgba(122, 92, 255, 0.22), transparent 62%),
    radial-gradient(680px 420px at 100% 10%, rgba(79, 107, 255, 0.24), transparent 62%),
    linear-gradient(180deg, #f7f8ff 0%, #e9edff 100%);
}

.login-card {
  width: 100%;
  max-width: 380px;
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, 0.8);
  background: rgba(255, 255, 255, 0.86);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  box-shadow: 0 24px 60px rgba(31, 42, 80, 0.18);
}

.login-card :deep(.el-card__body) {
  padding: 24px;
}

/* 标题用品牌渐变，正文仍是深色，避免整页都在发光 */
.login-card h2 {
  margin: 2px 0 14px;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.01em;
  background: linear-gradient(120deg, #1b1f2a 18%, var(--fc-brand) 68%, var(--fc-brand-2));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.login-card :deep(.el-input__wrapper) {
  min-height: 44px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
}

.login-card :deep(.el-button--primary) {
  height: 44px;
  border: 0;
  border-radius: 12px;
  font-weight: 600;
  background: linear-gradient(135deg, var(--fc-brand), var(--fc-brand-2));
  box-shadow: 0 10px 24px var(--fc-glow);
}

.login-card :deep(.el-button--primary:hover) {
  background: linear-gradient(135deg, #5f79ff, #8a6bff);
}

.hint {
  margin-top: 12px;
  font-size: 12px;
  color: var(--fc-text-2);
  text-align: center;
}
</style>
