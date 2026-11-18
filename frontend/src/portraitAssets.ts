/**
 * Frontend-only portrait asset allowlist shared by the public spectator and the
 * authenticated dashboard. Ids are server-owned catalog identifiers; an unknown
 * id must never be turned into an asset URL, so the mapping stays a strict
 * allowlist rather than a URL template.
 */
export const PORTRAIT_ASSET_SOURCES: Readonly<Record<string, string>> = {
  shieldbearer: '/assets/arena-v2/assets/portraits/shieldbearer.webp',
  shieldmaiden: '/assets/arena-v2/assets/portraits/shieldmaiden.webp',
  'iron-helm': '/assets/arena-v2/assets/portraits/iron-helm.webp',
  wanderer: '/assets/arena-v2/assets/portraits/wanderer.webp',
  skald: '/assets/arena-v2/assets/portraits/skald.webp',
  'fjord-scout': '/assets/arena-v2/assets/portraits/fjord-scout.webp',
  'raven-seer': '/assets/arena-v2/assets/portraits/raven-seer.webp',
  berserker: '/assets/arena-v2/assets/portraits/berserker.webp',
  'one-eyed-jarl': '/assets/arena-v2/assets/portraits/one-eyed-jarl.webp',
  valkyrie: '/assets/arena-v2/assets/portraits/valkyrie.webp',
  einherjar: '/assets/arena-v2/assets/portraits/einherjar.webp',
  'forge-keeper': '/assets/arena-v2/assets/portraits/forge-keeper.webp',
};

/** Bundled portrait URL for an allowlisted id, otherwise `null`. */
export function portraitAssetSrc(id: string): string | null {
  return Object.hasOwn(PORTRAIT_ASSET_SOURCES, id) ? PORTRAIT_ASSET_SOURCES[id] : null;
}
