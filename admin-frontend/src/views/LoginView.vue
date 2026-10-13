<template>
  <div class="login-page">
    <el-card class="login-card">
      <h2>智能客服 · 管理后台</h2>
      <p class="sub">客服 / 运维人员登录</p>
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
      </el-form>
    </el-card>
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
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #1f2d3d;
}
.login-card {
  width: 380px;
}
.sub {
  color: #909399;
  margin-top: -8px;
  margin-bottom: 16px;
  font-size: 13px;
}
</style>
