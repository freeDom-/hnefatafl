import { env } from 'node:process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, type Plugin } from 'vitest/config';
import react from '@vitejs/plugin-react';

const frontend = dirname(fileURLToPath(import.meta.url));

/**
 * Record which source modules each emitted chunk contains. The production probe
 * uses this build-only ownership map to prove a prohibited feature was not
 * folded into an eager shared chunk even when its generated filename lost the
 * feature prefix. It is written outside `dist`, so it never ships to clients and
 * never enters the bundle fingerprint.
 */
function bundleModuleOwnership(): Plugin {
  return {
    name: 'hnefatafl-bundle-module-ownership',
    apply: 'build',
    writeBundle(_options, bundle) {
      const chunks: Record<string, string[]> = {};
      for (const [fileName, output] of Object.entries(bundle)) {
        if (output.type !== 'chunk') continue;
        chunks[fileName] = Object.keys(output.modules).sort();
      }
      const target = resolve(frontend, '.cache/bundle-modules.json');
      mkdirSync(dirname(target), { recursive: true });
      writeFileSync(target, JSON.stringify({ chunks }, null, 2) + '\n');
    },
  };
}

export default defineConfig({
  plugins: [react(), bundleModuleOwnership()],
  build: {
    manifest: true,
    rollupOptions: {
      // Phase 4 browser-analysis runtime contract: its own lazy chunk, never part
      // of the initial application graph. Ordinary routes never import it.
      input: ['index.html', 'src/browserAnalysis/entry.ts'],
      preserveEntrySignatures: 'exports-only',
    },
  },
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
