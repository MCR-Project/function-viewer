import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  // Render serves dist/ at "/"; GitHub Pages serves it under "/function-viewer/" (set via VITE_BASE_PATH).
  base: process.env.VITE_BASE_PATH ?? '/',
  plugins: [react()],
})
