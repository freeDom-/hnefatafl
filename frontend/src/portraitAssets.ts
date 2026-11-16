/**
 * Frontend-only portrait asset allowlist. Ids are server-owned catalog
 * identifiers; an unknown id must never be turned into an asset URL, so the
 * mapping stays a strict allowlist rather than a URL template.
 */
export const PORTRAIT_ASSETS = new Set([
  'shieldbearer',
  'shieldmaiden',
  'iron-helm',
  'wanderer',
  'skald',
  'fjord-scout',
  'raven-seer',
  'berserker',
  'one-eyed-jarl',
  'valkyrie',
  'einherjar',
  'forge-keeper',
]);

/** Bundled portrait URL for an allowlisted id, otherwise `null`. */
export function portraitAssetSrc(id: string): string | null {
  return PORTRAIT_ASSETS.has(id) ? `/assets/arena-v2/assets/portraits/${id}.webp` : null;
}
