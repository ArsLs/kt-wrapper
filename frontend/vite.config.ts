import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Dev: `npm run dev` proxies /api to the FastAPI dev server on :8000.
// Prod: `npm run build` writes dist/, which FastAPI serves.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
});
