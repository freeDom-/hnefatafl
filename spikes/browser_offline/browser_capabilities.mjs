const { chromium } = await import(
  new URL("../../frontend/node_modules/playwright/index.mjs", import.meta.url)
);
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const output = resolve(process.argv[2] || "work/browser-neural-audit");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
});
try {
  const context = await browser.newContext();
  const csp =
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'";
  const authSource = await readFile(
    new URL("../../hnefatafl/auth.py", import.meta.url),
    "utf8",
  );
  if (!authSource.includes(csp))
    throw new Error(
      "Current CSP differs from the recorded probe; update deliberately",
    );
  await context.route("https://neural-probe.invalid/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({
      headers:
        path === "/restricted" || path === "/worker.js"
          ? { "Content-Security-Policy": csp }
          : {},
      contentType: path === "/worker.js" ? "text/javascript" : "text/html",
      body:
        path === "/worker.js"
          ? 'onmessage = () => postMessage("ready");'
          : "<!doctype html><title>Local capability probe</title>",
    });
  });
  const page = await context.newPage();
  await page.goto("https://neural-probe.invalid/");
  const info = await page.evaluate(async () => {
    const gpu = navigator.gpu;
    let adapter = null;
    let adapterError = null;
    if (gpu) {
      try {
        adapter = await Promise.race([
          gpu.requestAdapter(),
          new Promise((_, reject) =>
            setTimeout(
              () => reject(new Error("Adapter probe timed out")),
              2000,
            ),
          ),
        ]);
      } catch (error) {
        adapterError = String(error);
      }
    }
    return {
      secureContext: isSecureContext,
      workerAPI: typeof Worker,
      webAssemblyAPI: typeof WebAssembly,
      crossOriginIsolated,
      sharedArrayBuffer: typeof SharedArrayBuffer,
      webGPUAPI: !!gpu,
      webGPUAdapterObtained: !!adapter,
      webGPUAdapterError: adapterError,
      hardwareConcurrency: navigator.hardwareConcurrency,
      deviceMemoryGiBHint: navigator.deviceMemory ?? null,
    };
  });
  const wasmWithoutCSP = await page.evaluate(async () => {
    try {
      await WebAssembly.compile(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]));
      return { ok: true };
    } catch (error) {
      return { ok: false, error: String(error) };
    }
  });
  await page.goto("https://neural-probe.invalid/restricted");
  const restricted = await page.evaluate(async () => {
    let wasm;
    try {
      await WebAssembly.compile(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]));
      wasm = { ok: true };
    } catch (error) {
      wasm = { ok: false, error: String(error) };
    }
    const sameOriginWorker = await new Promise((resolve) => {
      const worker = new Worker("/worker.js");
      const timer = setTimeout(() => {
        worker.terminate();
        resolve(false);
      }, 2000);
      worker.onmessage = () => {
        clearTimeout(timer);
        worker.terminate();
        resolve(true);
      };
      worker.onerror = () => {
        clearTimeout(timer);
        worker.terminate();
        resolve(false);
      };
      worker.postMessage("probe");
    });
    return { wasm, sameOriginWorker };
  });
  const report = {
    cspProbe: {
      source:
        "literal current auth.py policy on intercepted HTTPS fixture, not a deployed response",
      policy: csp,
      wasmWithoutCSP,
      restricted,
    },
    browser: browser.version(),
    context:
      "headless cloud Chromium; intercepted secure origin with no app headers/model/runtime",
    ...info,
    caveat:
      "API/adapter capability probe only; not ORT initialization, operator coverage, physical-device support or neural execution.",
  };
  await writeFile(
    resolve(output, "browser-capabilities.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report, null, 2));
  await context.close();
} finally {
  await browser.close();
}
