import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Which backend each server proxies to, overridable by env var. This exists so a
// second dev stack can run against a second backend while the first stays up:
// restarting a backend to pick up a code change should not be able to interrupt
// someone demoing the app on the other one.
const API_TARGET = process.env.VITE_API_TARGET ?? 'http://localhost:9400'
const PREVIEW_API_TARGET = process.env.VITE_PREVIEW_API_TARGET ?? 'http://localhost:9400'

// `/ws` needs `ws: true`, and needs proxying in *preview* as well as dev: a production
// build has no dev port to reach past, so it opens the socket against its own origin
// (see hooks/useJobStream.ts) and the preview server has to forward it. Without this a
// built app runs fine until a job starts and then never shows progress.
const proxy = (target: string) => ({
  '/api': { target, changeOrigin: true },
  '/ws': { target: target.replace(/^http/, 'ws'), ws: true },
})

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: proxy(API_TARGET),
  },
  preview: {
    port: 4173,
    proxy: proxy(PREVIEW_API_TARGET),
  },
})
