import type { ReactNode } from 'react';
import { useI18n } from './i18n';
import { sideGlyph } from './sideGlyph';
import type { Side } from './types';

const sideLabels: Record<Side, string> = { attackers: 'Attackers', defenders: 'Defenders' };

export type ArmyRow = {
  side: Side;
  symbol: ReactNode;
  name: string;
  detail: string;
  toMove: boolean;
};

export default function ArmiesPanel({
  rows,
  captured,
  label,
}: {
  rows: ArmyRow[];
  captured: { attackers: number; defenders: number };
  label?: string;
}) {
  const { t } = useI18n();
  return (
    <section className="panel armies" aria-label={label}>
      {rows.map((row) => (
        <div className={`army ${row.toMove ? 'to-move' : ''}`} key={row.side}>
          <div className="army-symbol">{row.symbol}</div>
          <div>
            <h3>{row.name}</h3>
            <p>{row.detail}</p>
          </div>
          {row.toMove && (
            <span
              className="turn-light"
              aria-label={t('{side} to move', { side: t(sideLabels[row.side]) })}
            />
          )}
        </div>
      ))}
      <div className="captured-row">
        <span>{t('Captured')}</span>
        <span>
          {sideGlyph('attackers')} {captured.attackers}
        </span>
        <span>
          {sideGlyph('defenders')} {captured.defenders}
        </span>
      </div>
    </section>
  );
}
