import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Documentation captures are explicit maintenance, not part of ordinary CI.
// The env flag also gates the real tuning/resume captures in training-tuning.
const dataDir = mkdtempSync(join(tmpdir(), 'hnefatafl-browser-docs-'));
export default defineConfig({
  testDir: './tests',
  testMatch: ['**/documentation-screenshots.docs.ts', '**/training-tuning.spec.ts'],
  fullyParallel: false,
  workers: 1,
  timeout: 180000,
  expect: { timeout: 10000 },
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    viewport: { width: 1920, height: 1080 },
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: '../.venv/bin/python tests/start_backend.py',
      url: 'http://127.0.0.1:8001/api/health',
      env: { HNEFATAFL_DATA_DIR: dataDir },
      reuseExistingServer: false,
      timeout: 60000,
    },
    {
      command: 'npm run dev -- --port 4173 --strictPort',
      url: 'http://127.0.0.1:4173',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8001' },
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
