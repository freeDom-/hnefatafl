import { env } from 'node:process';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    // Multi-dialog workflows include accessible-name queries over the full UI.
    // Allow shared CI runners enough time without weakening request assertions.
    testTimeout: 10000,
    setupFiles: './src/test-setup.ts',
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
