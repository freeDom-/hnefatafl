import { useEffect, useState, type CSSProperties } from 'react';
import { PORTRAIT_ASSET_SOURCES } from './portraitAssets';
import './portrait.css';

/** Gallery counterparts are separately generated native 1024px artwork. */
export const PORTRAIT_GALLERY_ASSETS: Readonly<
  Record<string, { src: string; width: number; height: number }>
> = Object.fromEntries(
  Object.entries(PORTRAIT_ASSET_SOURCES).map(([id, src]) => [
    id,
    { src: src.replace('.webp', '-large.webp'), width: 1024, height: 1024 },
  ]),
);

/** Renders an allowlisted local portrait, falling back when the id is unknown. */
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
  const safeId = id && Object.hasOwn(PORTRAIT_ASSET_SOURCES, id) ? id : 'shieldbearer';
  const asset = gallery
    ? PORTRAIT_GALLERY_ASSETS[safeId]
    : { src: PORTRAIT_ASSET_SOURCES[safeId], width: 320, height: 320 };
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
