import { useState, type ReactNode } from 'react';
import { useCompactPlay, useMediaQuery } from './mobilePlay';
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
  const compact = useCompactPlay();
  const short = useMediaQuery('(max-height: 719px)');
  const pageSize = short ? 1 : 2;
  const anchor = entries.find((entry) => entry.current)?.ply ?? entries.at(-1)?.ply ?? 0;
  const anchorIndex = Math.max(
    0,
    entries.findIndex((entry) => entry.ply === anchor),
  );
  const lastPage = Math.max(0, Math.ceil(entries.length / pageSize) - 1);
  const [paging, setPaging] = useState({
    anchor,
    pageSize,
    page: Math.floor(anchorIndex / pageSize),
  });
  const page = Math.min(
    lastPage,
    paging.anchor === anchor && paging.pageSize === pageSize
      ? paging.page
      : Math.floor(anchorIndex / pageSize),
  );
  const showPage = (next: number) => setPaging({ anchor, pageSize, page: next });
  const visibleEntries = compact ? entries.slice(page * pageSize, (page + 1) * pageSize) : entries;
  return (
    <aside className={`panel history-panel${compact ? ' compact-history' : ''}`}>
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
        {visibleEntries.map((entry) => (
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
      {compact && (
        <nav className="mobile-history-pager" aria-label={t('Move history pages')}>
          <button
            className="icon-button"
            aria-label={t('Earlier moves')}
            disabled={page === 0}
            onClick={() => showPage(page - 1)}
          >
            ‹
          </button>
          <span aria-live="polite">
            {entries.length
              ? t('{from}–{to} of {count}', {
                  from: page * pageSize + 1,
                  to: Math.min((page + 1) * pageSize, entries.length),
                  count,
                })
              : t('No moves yet')}
          </span>
          {startAction && (
            <button
              className="icon-button"
              aria-label={t('↤ Starting position')}
              onClick={startAction.onClick}
              disabled={startAction.disabled}
            >
              ↤
            </button>
          )}
          <button
            className="text-button"
            onClick={() => showPage(lastPage)}
            disabled={page === lastPage}
          >
            {t('Latest')}
          </button>
          <button
            className="icon-button"
            aria-label={t('Later moves')}
            disabled={page === lastPage}
            onClick={() => showPage(page + 1)}
          >
            ›
          </button>
        </nav>
      )}
      {!compact && startAction && (
        <button
          className="history-start text-button"
          onClick={startAction.onClick}
          disabled={startAction.disabled}
        >
          {t('↤ Starting position')}
        </button>
      )}
      {!compact && footer}
      {!compact && children}
    </aside>
  );
}
