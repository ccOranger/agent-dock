<template>
  <div class="not-found">
    <p class="nf-code">404</p>
    <h1 class="nf-title">页面不存在</h1>
    <p class="muted">地址可能已变更，或这个模块已经不在了。</p>
    <el-button type="primary" @click="goHome">回到首页</el-button>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

// 首页会由路由守卫重定向到当前账号第一个可访问的模块
function goHome() {
  router.push('/')
}
</script>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 60vh;
  text-align: center;
}

.nf-code {
  margin: 0;
  font-size: 56px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.02em;
  color: var(--cs-border);
}

.nf-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--cs-text-1);
}

.not-found .muted {
  margin: 0 0 8px;
}
</style>
