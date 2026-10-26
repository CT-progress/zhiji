import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发：Vite 跑 5173，/api 与 /static 反代到本地 FastAPI（:8000）
// 生产：vite build 产物落 dist/，由 FastAPI 托管
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
