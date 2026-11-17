import { useEffect, useState } from 'react';
import { PORTRAIT_ASSET_SOURCES } from './portraitAssets';
import './portrait.css';

/** Renders an allowlisted local portrait, falling back when the id is unknown. */
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
    id && Object.hasOwn(PORTRAIT_ASSET_SOURCES, id)
      ? PORTRAIT_ASSET_SOURCES[id]
      : PORTRAIT_ASSET_SOURCES.shieldbearer;
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [path]);
  return failed ? (
    <span
      className={`portrait-art portrait-fallback ${className}`}
      role={name ? 'img' : undefined}
      aria-label={name || undefined}
      aria-hidden={name ? undefined : true}
    >
      <span aria-hidden="true">ᛟ</span>
    </span>
  ) : (
    <img
      className={`portrait-art ${className}`}
      src={path}
      alt={name}
      width={320}
      height={320}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}
