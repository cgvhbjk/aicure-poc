import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Relative base so the built assets resolve whether the app is served at the
// domain root (standalone) or under /pipeline (proxied behind the CRM).
export default defineConfig({
  base: './',
  plugins: [react()],
})
