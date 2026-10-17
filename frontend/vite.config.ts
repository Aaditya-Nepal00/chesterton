import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
    },
    // the Install page imports ../examples/chesterton.yml (outside frontend/)
    fs: { allow: ['..'] },
  },
  build: {
    chunkSizeWarningLimit: 900,
  },
})
