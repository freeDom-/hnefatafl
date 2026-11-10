/** Reproducible production React/SVG timings; no server, accounts or training required. */
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { cpus, availableParallelism, totalmem } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build, preview } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from '@playwright/test';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(frontend, '..');
const option = (name, fallback) =>
  process.argv.find((value) => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const counts = option('counts', '1500,10000,50000,100000').split(',').map(Number);
const samples = Number(option('samples', '3'));
if (
  !Number.isInteger(samples) ||
  samples < 1 ||
  counts.some((count) => !Number.isInteger(count) || count < 1 || count > 100000)
)
  throw new Error('Use positive sample counts and iteration counts between 1 and 100000.');
const label = option('label', 'current');
const legacy = option('legacy', 'false') === 'true';
const output = resolve(repository, option('output', `work/learning-benchmark-${label}.json`));
const outDir = resolve(repository, 'work/learning-benchmark-site');
const fingerprint = createHash('sha256');
for (const path of [
  'TrainingAnalysisViews.tsx',
  'TrainingHistoryChart.tsx',
  'TrainingMetricChart.tsx',
  'trainingLearningData.ts',
  'trainingDashboardData.ts',
])
  fingerprint.update(readFileSync(resolve(frontend, 'src', path)));

await build({
  root: frontend,
  configFile: false,
  plugins: [react()],
  logLevel: 'warn',
  build: {
    outDir,
    emptyOutDir: true,
    rollupOptions: { input: resolve(frontend, 'benchmarks/index.html') },
  },
});
const server = await preview({
  root: frontend,
  configFile: false,
  logLevel: 'warn',
  build: { outDir },
  preview: { host: '127.0.0.1', port: 4178, strictPort: true },
});
let browser;
const results = [];
try {
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    args: ['--enable-precise-memory-info'],
  });
  for (const iterations of counts)
    for (let repetition = 1; repetition <= samples; repetition++) {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      page.setDefaultTimeout(120000);
      page.on('pageerror', (error) => {
        throw error;
      });
      const cdp = await page.context().newCDPSession(page);
      const heap = async () => {
        await cdp.send('HeapProfiler.collectGarbage');
        return (await cdp.send('Runtime.getHeapUsage')).usedSize;
      };
      await page.goto('http://127.0.0.1:4178/benchmarks/index.html');
      await page.waitForFunction(() => !!window.learningBenchmark);
      await page.evaluate(() => document.fonts.ready);
      const idleHeap = await heap();
      const input = await page.evaluate(
        ({ count, legacy }) => window.learningBenchmark.prepare(count, legacy),
        { count: iterations, legacy },
      );
      const inputHeap = await heap();
      const reconstructionMs = await page.evaluate(() => window.learningBenchmark.reconstruct());
      const derivedHeap = await heap();
      const availability = await page.evaluate(() => window.learningBenchmark.availability());
      await page.evaluate(() => window.learningBenchmark.releaseDerived());
      const rawHeap = await heap();
      const initial = await page.evaluate(() => window.learningBenchmark.mount());
      const mountedHeap = await heap();
      const metrics = {};
      for (const key of [
        'weight_decay',
        'batch_size',
        'mcts_iterations',
        'learning_rate',
        'loss',
      ]) {
        metrics[key] = await page.evaluate((key) => window.learningBenchmark.metric(key), key);
      }
      const range250 = await page.evaluate(() => window.learningBenchmark.range(250));
      const range10 = await page.evaluate(() => window.learningBenchmark.range(10));
      const rangeAll = await page.evaluate(() => window.learningBenchmark.range(0));
      const append = await page.evaluate(() => window.learningBenchmark.append());
      const heartbeat = await page.evaluate(() => window.learningBenchmark.heartbeat());
      const updatedHeap = await heap();
      const dom = await cdp.send('Memory.getDOMCounters');
      const summary = await page.evaluate(() => window.learningBenchmark.summary());
      if (
        summary.tables !== 0 ||
        summary.charts !== 1 ||
        summary.rows !== iterations + 1 ||
        availability.count !== 20
      )
        throw new Error(`Unexpected rendered data: ${JSON.stringify(summary)}`);
      const result = {
        iterations,
        repetition,
        ...input,
        reconstructionMs,
        availabilityMs: availability.ms,
        initial,
        metrics,
        range250,
        range10,
        rangeAll,
        append,
        heartbeat,
        heapBytes: {
          idle: idleHeap,
          input: inputHeap,
          derived: derivedHeap,
          raw: rawHeap,
          mounted: mountedHeap,
          updated: updatedHeap,
        },
        dom,
        summary,
      };
      results.push(result);
      console.log(
        JSON.stringify({
          iterations,
          repetition,
          reconstructMs: reconstructionMs,
          initialMs: initial.commitMs,
          appendMs: append.commitMs,
          retainedMiB: (mountedHeap - rawHeap) / 1024 ** 2,
        }),
      );
      await page.close();
    }
  const report = {
    label,
    legacy,
    sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], {
      cwd: repository,
      encoding: 'utf8',
    }).trim(),
    sourceFingerprint: fingerprint.digest('hex'),
    recordedAt: new Date().toISOString(),
    browser: browser.version(),
    node: process.version,
    cpu: cpus()[0]?.model,
    cpus: availableParallelism(),
    totalMemoryBytes: totalmem(),
    viewport: { width: 1280, height: 900 },
    results,
  };
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(output, `${JSON.stringify(report, null, 2)}\n`);
  console.log(`Saved ${output}`);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.httpServer.close(resolve));
}
