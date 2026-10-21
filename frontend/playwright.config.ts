import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dataDir = mkdtempSync(join(tmpdir(), 'hnefatafl-browser-'));
export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  timeout: 90000,
  expect: { timeout: 10000 },
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: [
    {
      command:
        '../.venv/bin/python -m uvicorn hnefatafl.server:app --host 127.0.0.1 --port 8001 --app-dir ..',
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
