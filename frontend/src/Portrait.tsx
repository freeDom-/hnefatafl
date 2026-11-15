import { useEffect, useState } from 'react';

/** Explicit local allowlist: never interpolate an account-controlled asset URL. */
export const PORTRAIT_ASSETS: Readonly<Record<string, string>> = {
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

export default function Portrait({
  id,
  name = '',
  className = '',
}: {
  id: string | null | undefined;
  /** Empty when the adjacent player/portrait name already labels the artwork. */
  name?: string;
  className?: string;
}) {
  const path =
    id && Object.hasOwn(PORTRAIT_ASSETS, id) ? PORTRAIT_ASSETS[id] : PORTRAIT_ASSETS.shieldbearer;
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [path]);
  return failed ? (
    <span
      className={`portrait-art portrait-fallback ${className}`}
      role={name ? 'img' : undefined}
      aria-label={name || undefined}
      aria-hidden={name ? undefined : true}
    >
      ᛟ
    </span>
  ) : (
    <img
      className={`portrait-art ${className}`}
      src={path}
      alt={name}
      width={256}
      height={256}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}
