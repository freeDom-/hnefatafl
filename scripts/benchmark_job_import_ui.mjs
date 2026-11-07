// The Python driver supplies only local fixture credentials on stdin.
import { readFileSync } from "node:fs";
import { chromium } from "../frontend/node_modules/playwright/index.mjs";

const config = JSON.parse(readFileSync(0, "utf8"));
const browser = await chromium.launch({ executablePath: config.chromium });
let interval;
let closing = false;
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    extraHTTPHeaders: { "X-CSRF-Token": config.csrf },
  });
  await context.addCookies(
    Object.entries(config.cookies).map(([name, value]) => ({
      name,
      value,
      url: config.origin,
    })),
  );
  await context.addInitScript(
    (account) => localStorage.setItem("hnefatafl-account-cache", account),
    config.account,
  );
  const page = await context.newPage();
  page.setDefaultTimeout(180000);
  await page.goto(config.origin + "/dashboard?setup=single-player");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "AI workshop", exact: true }).click();
  await page.getByLabel("Import job", { exact: true }).waitFor();
  const cdp = await context.newCDPSession(page);
  await cdp.send("Performance.enable");
  await cdp.send("HeapProfiler.collectGarbage");
  const heap = async () =>
    (await cdp.send("Performance.getMetrics")).metrics.find(
      (metric) => metric.name === "JSHeapUsedSize",
    ).value;
  const initial = await heap();
  let peak = initial;
  let measuring = false;
  const sample = async () => {
    if (measuring) return;
    measuring = true;
    try {
      peak = Math.max(peak, await heap());
    } catch (error) {
      if (!closing) throw error;
    } finally {
      measuring = false;
    }
  };
  interval = setInterval(() => void sample(), 20);
  const pending = new Set();
  let details = 0,
    detailBytes = 0,
    importBytes = 0,
    importSeconds;
  let activity = performance.now();
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/training/jobs")) return;
    pending.add(request);
    activity = performance.now();
    if (
      request.method() === "GET" &&
      /^\/api\/training\/jobs\/[0-9a-f]{32}$/.test(path)
    )
      details++;
  });
  page.on("requestfinished", (request) => {
    pending.delete(request);
    activity = performance.now();
  });
  page.on("requestfailed", (request) => {
    pending.delete(request);
    activity = performance.now();
  });
  const started = performance.now();
  page.on("response", async (response) => {
    const path = new URL(response.url()).pathname;
    if (path === "/api/training/jobs/import") {
      importBytes = Number(response.headers()["content-length"]);
      await response.finished();
      importSeconds = (performance.now() - started) / 1000;
    } else if (/^\/api\/training\/jobs\/[0-9a-f]{32}$/.test(path)) {
      detailBytes += Number(response.headers()["content-length"]);
    }
  });
  await page
    .getByLabel("Import job", { exact: true })
    .setInputFiles(config.archive);
  await page.locator(".job-row.selected").waitFor();
  const selected = (performance.now() - started) / 1000;
  await page.waitForFunction(() =>
    document
      .querySelector(".dashboard-progress strong")
      ?.textContent.includes("1000"),
  );
  const deadline = performance.now() + 180000;
  while (pending.size || performance.now() - activity < 350 || !importSeconds) {
    if (performance.now() > deadline)
      throw new Error("Import/detail request did not finish");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  await page
    .getByText("Loading history…", { exact: true })
    .waitFor({ state: "hidden" });
  const loaded = (performance.now() - started) / 1000;
  await sample();
  clearInterval(interval);
  await cdp.send("HeapProfiler.collectGarbage");
  const retained = await heap();
  console.log(
    JSON.stringify({
      browser: browser.version(),
      import_response_bytes: importBytes,
      full_detail_requests: details,
      detail_response_bytes: detailBytes,
      import_seconds: importSeconds,
      selection_seconds: selected,
      initial_ui_seconds: loaded,
      initial_js_heap_bytes: initial,
      observed_peak_js_heap_bytes: peak,
      retained_js_heap_bytes: retained,
    }),
  );
} finally {
  closing = true;
  clearInterval(interval);
  await browser.close();
}
