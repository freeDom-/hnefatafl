import { useEffect, useState, type CSSProperties } from 'react';
import './portrait.css';

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

/** Gallery counterparts are separately generated native 1024px artwork. */
export const PORTRAIT_GALLERY_ASSETS: Readonly<
  Record<string, { src: string; width: number; height: number }>
> = Object.fromEntries(
  Object.entries(PORTRAIT_ASSETS).map(([id, src]) => [
    id,
    { src: src.replace('.webp', '-large.webp'), width: 1024, height: 1024 },
  ]),
);

export default function Portrait({
  id,
  name = '',
  className = '',
  gallery = false,
}: {
  id: string | null | undefined;
  /** Empty when the adjacent player/portrait name already labels the artwork. */
  name?: string;
  className?: string;
  gallery?: boolean;
}) {
  const safeId = id && Object.hasOwn(PORTRAIT_ASSETS, id) ? id : 'shieldbearer';
  const asset = gallery
    ? PORTRAIT_GALLERY_ASSETS[safeId]
    : { src: PORTRAIT_ASSETS[safeId], width: 320, height: 320 };
  const path = asset.src;
  const style = gallery
    ? ({ '--portrait-native-width': `${asset.width}px` } as CSSProperties)
    : undefined;
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [path]);
  return failed ? (
    <span
      className={`portrait-art portrait-fallback ${className}`}
      style={style}
      role={name ? 'img' : undefined}
      aria-label={name || undefined}
      aria-hidden={name ? undefined : true}
    >
      <span aria-hidden="true">ᛟ</span>
    </span>
  ) : (
    <img
      className={`portrait-art ${className}`}
      style={style}
      src={path}
      alt={name}
      width={asset.width}
      height={asset.height}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}
