import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 管理后台：独立端口 5174，与客户前端(5173)分离，共用同一后端。
// 生产镜像由 Nginx 挂在 /admin/ 下（构建时 VITE_BASE=/admin/），开发模式保持根路径。
export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@shared': fileURLToPath(new URL('../packages/shared/src', import.meta.url)),
    },
  },
  server: {
    host: 'localhost',
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // Prometheus 指标端点（后端 main.py 根路径 /metrics，无需鉴权），
      // Dashboard 通过相对链接 /metrics 访问，必须显式代理，否则被 Vite SPA 回退吞掉。
      '/metrics': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
