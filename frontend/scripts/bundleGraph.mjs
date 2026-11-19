/**
 * Shared production manifest traversal for the bundle budget and the network
 * probe. Keeping one implementation means the checker and the runtime evidence
 * agree on what "initial graph" means and cannot silently drift apart.
 */

/** The inline first-paint script is referenced by index.html, not the manifest. */
export const APPEARANCE_INIT = 'assets/appearance-init.js';

/** Find the single manifest chunk with a route name, failing loudly otherwise. */
export function routeChunk(manifest, name) {
  const matches = Object.entries(manifest).filter(([, chunk]) => chunk.name === name);
  if (matches.length !== 1) throw new Error(`Expected exactly one ${name} entry in the manifest.`);
  return matches[0];
}

/**
 * Walk the eager manifest graph for a document entry plus one route-selected
 * chunk. Vite marks route entries as dynamic imports, but the selected route is
 * requested immediately, so its complete eager graph belongs to first load.
 */
export function collectRouteGraph(manifest, routeName, entry = 'index.html') {
  const visited = new Set();
  const scripts = new Set([APPEARANCE_INIT]);
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
  if (routeName) walk(routeChunk(manifest, routeName)[0]);
  return { scripts, styles, visited };
}

/**
 * The authenticated Dashboard initial graph. The route-selected
 * `AuthenticatedArena` chunk is counted even though Vite marks it dynamic.
 */
export function collectInitialGraph(manifest, entry = 'index.html') {
  return collectRouteGraph(manifest, 'AuthenticatedArena', entry);
}

/** Normalize an observed request path or manifest file to `assets/name.ext`. */
export function normalizeAssetPath(value) {
  const pathname = value.startsWith('http') ? new URL(value).pathname : value;
  return pathname.replace(/^\/+/, '').split('?')[0];
}

/**
 * Compare an expected initial graph with the assets a browser actually loaded.
 * Extra observed assets are unexpected eager or delayed loads; missing expected
 * assets mean the document never completed its real initial graph.
 */
export function compareInitialGraph(expected, observed) {
  const expectedSet = new Set([...expected].map(normalizeAssetPath));
  const observedSet = new Set([...observed].map(normalizeAssetPath));
  return {
    unexpected: [...observedSet].filter((asset) => !expectedSet.has(asset)).sort(),
    missing: [...expectedSet].filter((asset) => !observedSet.has(asset)).sort(),
  };
}
