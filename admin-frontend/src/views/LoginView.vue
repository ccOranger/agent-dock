<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">
        <span class="brand-mark">AD</span>
        <div class="brand-text">
          <h2>AgentDock 管理后台</h2>
          <p>运营 / 运维人员登录</p>
        </div>
      </div>
      <el-form :model="form" @submit.prevent="onLogin">
        <el-form-item>
          <el-input v-model="form.username" size="large" placeholder="用户名" />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.password"
            size="large"
            type="password"
            placeholder="密码"
            show-password
            @keyup.enter="onLogin"
          />
        </el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" @click="onLogin">
          登录
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const auth = useAuthStore()
const router = useRouter()

async function onLogin() {
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
</script>

<style scoped>
/* 登录页沿用深色侧栏的色系，保持整体品牌一致 */
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: radial-gradient(1200px 500px at 50% -10%, #2b3f57 0%, var(--cs-sidebar-bg) 60%);
}

.login-card {
  width: 380px;
  padding: 28px 28px 24px;
  border-radius: var(--cs-radius-lg);
  background: var(--cs-surface);
  box-shadow: var(--cs-shadow-pop);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--cs-radius-md);
  background: linear-gradient(135deg, var(--cs-brand), var(--cs-brand-strong));
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.brand-text h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--cs-text-1);
}

.brand-text p {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--cs-text-3);
}
</style>
