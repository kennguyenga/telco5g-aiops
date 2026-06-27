import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api/orchestrator': { target: 'http://localhost:9001', rewrite: (p) => p.replace(/^\/api\/orchestrator/, '/api') },
      '/api/collector':    { target: 'http://localhost:9000', rewrite: (p) => p.replace(/^\/api\/collector/, '/api') },
      '/api/ml':           { target: 'http://localhost:9002', rewrite: (p) => p.replace(/^\/api\/ml/, '/api/ml') },
      '/api/llm':          { target: 'http://localhost:9003', rewrite: (p) => p.replace(/^\/api\/llm/, '/api/llm') },
    },
  },
})
