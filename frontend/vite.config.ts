import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The chat API (backend/, ADR 005); ports 4000 and 8000 are taken on the team's machines
const API_URL = 'http://localhost:8765'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true, // fail loudly instead of moving to another port
    // One origin for the browser, so the backend needs no CORS settings
    proxy: { '/chat': API_URL, '/session': API_URL, '/profile': API_URL, '/models': API_URL, '/health': API_URL },
  },
})
