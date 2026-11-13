import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development, the FastAPI backend runs on :8000 and Vite proxies API calls to it.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/s': 'http://localhost:8000',
    },
  },
})
