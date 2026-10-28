import type { ReactNode } from 'react';
import { useI18n } from './i18n';
import { sideGlyph } from './sideGlyph';
import type { Side } from './types';

export type HistoryEntry = {
  ply: number;
  notation: string;
  side: Side;
  current: boolean;
  future: boolean;
  onSeek: () => void;
  disabled?: boolean;
};

export default function HistoryPanel({
  count,
  entries,
  startAction,
  footer,
  children,
}: {
  count: number;
  entries: HistoryEntry[];
  startAction?: { onClick: () => void; disabled: boolean };
  footer?: ReactNode;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <aside className="panel history-panel">
      <div className="panel-title">
        <h2>{t('The saga so far')}</h2>
        <span className="badge">{count}</span>
      </div>
      <p className="subtle">{t('A record of every move')}</p>
      <div className="history-columns">
        <span>{t('MOVE')}</span>
        <span>{t('JOURNEY')}</span>
        <span>{t('SIDE')}</span>
      </div>
      <ol className="move-list">
        {entries.map((entry) => (
          <li key={entry.ply} className={entry.current ? 'current' : entry.future ? 'future' : ''}>
            <button
              type="button"
              onClick={entry.onSeek}
              disabled={entry.disabled}
              aria-label={t('Replay to move {move}', { move: entry.ply })}
            >
              <span>{String(entry.ply).padStart(2, '0')}</span>
              <strong>{entry.notation}</strong>
              {sideGlyph(entry.side)}
            </button>
          </li>
        ))}
      </ol>
      {entries.length === 0 && (
        <div className="history-empty">
          <span aria-hidden="true">ᛝ</span>
          <p>
            {t('Every saga starts')}
            <br />
            {t('with a first move.')}
          </p>
        </div>
      )}
      {startAction && (
        <button
          className="history-start text-button"
          onClick={startAction.onClick}
          disabled={startAction.disabled}
        >
          {t('↤ Starting position')}
        </button>
      )}
      {footer}
      {children}
    </aside>
  );
}
