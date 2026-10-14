<template>
  <div class="login-page">
    <el-card class="login-card">
      <h2>智能客服系统</h2>
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
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const mode = ref<'login' | 'register'>('login')
const auth = useAuthStore()
const router = useRouter()

async function onLogin() {
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    router.push('/chat')
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
    router.push('/chat')
  } catch (e: any) {
    ElMessage.error(e?.message || '注册失败')
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
  background: #f0f2f5;
}
.login-card {
  width: 360px;
}
.hint {
  margin-top: 12px;
  font-size: 12px;
  color: #909399;
  text-align: center;
}
</style>
