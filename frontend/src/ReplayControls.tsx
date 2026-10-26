import { useI18n } from './i18n';
/** Viewer state only; no game, account, guest, analysis or seek adapter. */
export default function ReplayControls({
  cursor,
  length,
  onChange,
  disabled = false,
}: {
  cursor: number;
  length: number;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  return (
    <nav className="online-toolbar" aria-label={t('Replay navigation')}>
      <button
        className="button secondary"
        disabled={disabled || cursor === 0}
        onClick={() => onChange(Math.max(0, cursor - 1))}
      >
        {t('Previous move')}
      </button>
      <span aria-live="polite">
        {t('Move {move} of {total}', { move: cursor, total: Math.max(0, length - 1) })}
      </span>
      <button
        className="button secondary"
        disabled={disabled || cursor >= length - 1}
        onClick={() => onChange(Math.min(length - 1, cursor + 1))}
      >
        {t('Next move')}
      </button>
    </nav>
  );
}
