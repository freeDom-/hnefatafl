import { useI18n } from './i18n';
/** Viewer state only; no game, account, guest, analysis or seek adapter. */
export default function ReplayControls({
  cursor,
  length,
  onChange,
  disabled = false,
  playback,
}: {
  cursor: number;
  length: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  playback?: { playing: boolean; toggle: () => void };
}) {
  const { t } = useI18n();
  const atEnd = cursor >= Math.max(0, length - 1);
  return (
    <nav className="online-toolbar" aria-label={t('Replay navigation')}>
      <button
        className="button secondary"
        disabled={disabled || cursor === 0}
        onClick={() => onChange(Math.max(0, cursor - 1))}
      >
        {t('Previous move')}
      </button>
      {playback && (
        <button
          className="button secondary"
          disabled={disabled || (!playback.playing && atEnd)}
          aria-pressed={playback.playing}
          onClick={playback.toggle}
        >
          {t(playback.playing ? 'Stop' : 'Play')}
        </button>
      )}
      <span aria-live="polite">
        {t('Move {move} of {total}', { move: cursor, total: Math.max(0, length - 1) })}
      </span>
      <button
        className="button secondary"
        disabled={disabled || atEnd}
        onClick={() => onChange(Math.min(length - 1, cursor + 1))}
      >
        {t('Next move')}
      </button>
    </nav>
  );
}
