import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { useCompactPlay, useDesktopPlay } from './mobilePlay';
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
  status,
  startAction,
  footer,
  children,
}: {
  count: number;
  entries: HistoryEntry[];
  status?: ReactNode;
  startAction?: { onClick: () => void; disabled: boolean };
  footer?: ReactNode;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  const compact = useCompactPlay();
  const desktop = useDesktopPlay();
  const list = useRef<HTMLOListElement>(null);
  const currentPly = entries.find((entry) => entry.current)?.ply;
  useLayoutEffect(() => {
    const node = list.current;
    const current = node?.querySelector('.current');
    if ((!compact && !desktop) || !node || !current) return;
    let observer: ResizeObserver | undefined;
    const reveal = () => {
      if (!node.clientHeight) return;
      const bounds = node.getBoundingClientRect();
      const row = current.getBoundingClientRect();
      // Scroll only the move list, keeping the board and document in place.
      if (row.bottom > bounds.bottom) node.scrollTop += row.bottom - bounds.bottom;
      else if (row.top < bounds.top) node.scrollTop += row.top - bounds.top;
      observer?.disconnect();
    };
    reveal();
    // A move may arrive while another tab hides the list. Reveal it on return.
    if (!node.clientHeight && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(reveal);
      observer.observe(node);
    }
    return () => observer?.disconnect();
  }, [compact, desktop, currentPly, entries.length]);
  return (
    <aside className={`panel history-panel${compact ? ' compact-history' : ''}`}>
      <div className="panel-title">
        <h2>{t('The saga so far')}</h2>
        <span className="badge">{count}</span>
      </div>
      <p className="subtle">{t('A record of every move')}</p>
      {status}
      <div className="history-columns">
        <span>{t('MOVE')}</span>
        <span>{t('JOURNEY')}</span>
        <span>{t('SIDE')}</span>
      </div>
      <ol className="move-list" ref={list}>
        {startAction && (
          <li>
            <button
              className="history-start"
              onClick={startAction.onClick}
              disabled={startAction.disabled}
            >
              {t('↤ Starting position')}
            </button>
          </li>
        )}
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
      {!compact && footer}
      {!compact && !desktop && children}
    </aside>
  );
}
