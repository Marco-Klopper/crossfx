import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Port 5173 is not incidental: the backend's CORS_ORIGINS allows
// http://localhost:5173 out of the box (backend/app/config.py), so the
// browser can call the API directly with no proxy and no backend change.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.{js,jsx}'],
  },
})
