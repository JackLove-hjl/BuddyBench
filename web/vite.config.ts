import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// dev 代理:/api、/images、/uploads → 本地后端(localhost:8000)
// 同源后无 CORS、图片相对路径天然可用、SSE 流式透传
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        // 侧栏终端的 WebSocket 也走 /api 前缀:不打开 ws,升级请求会被 dev server 丢掉
        ws: true,
      },
      '/images': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
      '/uploads': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
