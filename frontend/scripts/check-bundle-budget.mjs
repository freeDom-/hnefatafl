import { readFile } from 'node:fs/promises';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
let dist = join(frontend, 'dist');
let entry = 'index.html';
for (const argument of process.argv.slice(2)) {
  if (argument.startsWith('--dist=')) dist = resolve(argument.slice(7));
  else if (argument.startsWith('--entry=')) entry = argument.slice(8);
  else throw new Error(`Unknown argument: ${argument}`);
}
const manifest = JSON.parse(await readFile(join(dist, '.vite', 'manifest.json'), 'utf8'));
const visited = new Set();
const scripts = new Set(['assets/appearance-init.js']);
const styles = new Set();
function walk(key) {
  if (visited.has(key)) return;
  visited.add(key);
  const chunk = manifest[key];
  if (!chunk) throw new Error(`Missing manifest entry: ${key}`);
  scripts.add(chunk.file);
  for (const css of chunk.css || []) styles.add(css);
  // Include transitive eager imports; optional dynamic features are independent.
  for (const dependency of chunk.imports || []) walk(dependency);
}
walk(entry);
async function sizes(paths) {
  let raw = 0;
  let gzip = 0;
  for (const path of paths) {
    const bytes = await readFile(join(dist, path));
    raw += bytes.length;
    gzip += gzipSync(bytes, { level: 9 }).length;
  }
  return { raw, gzip, files: [...paths].sort() };
}
const javascript = await sizes(scripts);
const css = await sizes(styles);
const budgets = { javascriptRaw: 600000, javascriptGzip: 200000, cssRaw: 150000, cssGzip: 35000 };
console.log(
  JSON.stringify(
    { entry, compression: 'gzip level9, each actual asset separately', javascript, css, budgets },
    null,
    2,
  ),
);
if (
  javascript.raw > budgets.javascriptRaw ||
  javascript.gzip > budgets.javascriptGzip ||
  css.raw > budgets.cssRaw ||
  css.gzip > budgets.cssGzip
) {
  throw new Error(
    'Initial bundle exceeds its measured budget; inspect eager imports before increasing limits.',
  );
}
