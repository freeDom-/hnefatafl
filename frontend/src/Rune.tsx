import type { PlayerConfig, StatRow } from './types';

/** Names from old game snapshots may predate a model-dropdown selection. */
export function participantName(
  player: Pick<PlayerConfig, 'name' | 'model_name' | 'kind'> | StatRow | undefined,
  fallback = '',
) {
  if (!player) return fallback;
  return (player.kind === 'ai' ? player.model_name : undefined) || player.name || fallback;
}

/** The server owns identity allocation; a missing rune never receives a made-up client identity. */
export default function Rune({ rune, className = '' }: { rune?: string; className?: string }) {
  return rune ? (
    <span
      className={`identity-rune ${className}`}
      aria-hidden="true"
      data-rune={rune}
      data-rune-size={Array.from(rune).length > 1 ? 'compound' : 'single'}
    >
      {rune}
    </span>
  ) : null;
}
