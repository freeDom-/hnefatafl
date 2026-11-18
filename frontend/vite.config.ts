import { env } from 'node:process';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { manifest: true },
  server: {
    proxy: {
      '/api': { target: env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    // Multi-dialog workflows include accessible-name queries over the full UI.
    // Allow shared CI runners and loaded hosts enough time without weakening
    // request assertions.
    testTimeout: 20000,
    // Keep Vitest 4's default maxWorkers (availableParallelism() - 1). A static
    // cap low enough to help a host that over-reports CPUs would slow genuine
    // high-core hosts; override per run with `--maxWorkers=<n>` when needed.
    setupFiles: './src/test-setup.ts',
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
