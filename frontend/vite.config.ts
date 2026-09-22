import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  // Render serves dist/ at "/"; GitHub Pages serves it under "/function-viewer/" (set via VITE_BASE_PATH).
  base: process.env.VITE_BASE_PATH ?? '/',
  plugins: [react()],
  server: {
    // Docker Desktop bind mounts don't reliably forward native FS change events; polling
    // is what makes HMR work for the frontend dev container (see compose.yaml).
    watch: process.env.VITE_USE_POLLING ? { usePolling: true } : undefined,
  },
})
