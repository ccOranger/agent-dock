<template>
  <div class="login-page">
    <!-- 背景装饰：品牌色光晕 + 极淡网格，只在登录页出现，不参与布局 -->
    <div class="bg-glow" aria-hidden="true" />
    <div class="bg-grid" aria-hidden="true" />

    <div class="login-card">
      <div class="brand">
        <span class="brand-mark">AD</span>
        <div class="brand-text">
          <h1>AgentDock 管理后台</h1>
          <p>运营 / 运维人员登录</p>
        </div>
      </div>

      <el-form
        ref="formRef"
        class="login-form"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="onLogin"
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            ref="usernameRef"
            v-model="form.username"
            size="large"
            placeholder="请输入用户名"
            autocomplete="username"
            :prefix-icon="User"
            @keyup.enter="onLogin"
          />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            size="large"
            type="password"
            placeholder="请输入密码"
            autocomplete="current-password"
            show-password
            :prefix-icon="Lock"
            @keyup.enter="onLogin"
          />
        </el-form-item>

        <el-button
          class="submit"
          type="primary"
          size="large"
          :loading="loading"
          @click="onLogin"
        >
          登录
        </el-button>
      </el-form>

      <p class="foot">仅限运营 / 运维人员使用 · 账号由管理员统一分配</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules, type InputInstance } from 'element-plus'
import { Lock, User } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const formRef = ref<FormInstance>()
const usernameRef = ref<InputInstance>()
const auth = useAuthStore()
const router = useRouter()

// 只做必填校验：账号规则由后端判定，登录页不复制一份密码策略
const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function onLogin() {
  if (loading.value) return
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await auth.login(form.username, form.password)
    router.push('/inquiries')
  } catch (e: any) {
    ElMessage.error(e?.message || '登录失败')
  } finally {
    loading.value = false
  }
}

// 进入页面直接聚焦用户名，少一次鼠标点击
onMounted(() => usernameRef.value?.focus())
</script>

<style scoped>
/* 深海军蓝底 + 品牌色光晕：与侧栏同族，但比纯色更有层次 */
.login-page {
  position: relative;
  min-height: 100vh;
  padding: 24px;
  display: grid;
  place-items: center;
  overflow: hidden;
  background: radial-gradient(1100px 560px at 50% -14%, #11406f 0%, #06203c 46%, #001529 100%);
}

.bg-glow {
  position: absolute;
  top: -360px;
  left: 50%;
  width: 760px;
  height: 760px;
  transform: translateX(-50%);
  border-radius: 50%;
  background: radial-gradient(circle, rgba(22, 119, 255, 0.32) 0%, rgba(22, 119, 255, 0) 62%);
  pointer-events: none;
}

.bg-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.05) 1px, transparent 1px);
  background-size: 44px 44px;
  -webkit-mask-image: radial-gradient(640px 440px at 50% 26%, #000 0%, transparent 78%);
  mask-image: radial-gradient(640px 440px at 50% 26%, #000 0%, transparent 78%);
}

.login-card {
  position: relative;
  width: 100%;
  max-width: 400px;
  padding: 32px 32px 20px;
  background: var(--cs-surface);
  border-radius: var(--cs-radius-lg);
  box-shadow:
    0 24px 60px rgba(0, 21, 41, 0.45),
    0 2px 8px rgba(0, 21, 41, 0.25);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  flex: none;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--cs-brand), var(--cs-brand-strong));
  box-shadow: 0 6px 16px rgba(22, 119, 255, 0.28);
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.brand-text h1 {
  margin: 0;
  font-size: 19px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--cs-text-1);
}

.brand-text p {
  margin: 3px 0 0;
  font-size: 13px;
  color: var(--cs-text-3);
}

/* 标签在上方：比纯 placeholder 更明确，也避免输入后丢失字段含义 */
.login-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.login-form :deep(.el-form-item__label) {
  padding-bottom: 6px;
  font-size: 13px;
  line-height: 1.4;
  color: var(--cs-text-2);
}

.submit {
  width: 100%;
  height: 42px;
  margin-top: 6px;
  font-size: 14px;
}

/* 页脚说明：与表单拉开，弱化为辅助信息 */
.foot {
  margin: 18px 0 0;
  padding-top: 14px;
  border-top: 1px solid var(--cs-border-light);
  text-align: center;
  font-size: 12px;
  color: var(--cs-text-3);
}

@media (max-width: 480px) {
  .login-card {
    padding: 24px 20px 16px;
  }
}
</style>
