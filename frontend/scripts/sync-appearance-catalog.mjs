// Keep the synchronous CSP-compatible bootstrap derived from the React catalog.
import { readFile, writeFile } from 'node:fs/promises';
import { format, resolveConfig } from 'prettier';
const path = new URL('../public/assets/appearance-init.js', import.meta.url);
const catalog = JSON.parse(
  await readFile(new URL('../src/appearanceCatalog.json', import.meta.url), 'utf8'),
);
const bootstrapCatalog = {
  defaultTheme: catalog.defaultTheme,
  legacyTheme: catalog.legacyTheme,
  themes: Object.fromEntries(Object.keys(catalog.themes).map((id) => [id, true])),
};
const source = await readFile(path, 'utf8');
const block =
  /\/\* BEGIN GENERATED APPEARANCE CATALOG \*\/[\s\S]*?\/\* END GENERATED APPEARANCE CATALOG \*\//;
if (!block.test(source)) throw new Error('Missing appearance catalog generation markers');
const updated = await format(
  source.replace(
    block,
    `/* BEGIN GENERATED APPEARANCE CATALOG */\nconst UI_THEME_CATALOG = ${JSON.stringify(bootstrapCatalog)};\n/* END GENERATED APPEARANCE CATALOG */`,
  ),
  { ...(await resolveConfig(path.pathname)), filepath: path.pathname },
);
if (process.argv.includes('--check')) {
  if (source !== updated)
    throw new Error(
      'Run node scripts/sync-appearance-catalog.mjs to synchronize first-paint themes',
    );
} else if (source !== updated) await writeFile(path, updated);
