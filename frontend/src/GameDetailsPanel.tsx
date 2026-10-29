import type { ReactNode } from 'react';
import { useDesktopPlay } from './mobilePlay';

export type GameDetailRow = { label: string; value: ReactNode };

export default function GameDetailsPanel({
  ruleset,
  size,
  header,
  rows,
  desktopRows,
  footer,
}: {
  ruleset: string;
  size: number;
  header?: ReactNode;
  rows: GameDetailRow[];
  desktopRows?: GameDetailRow[];
  footer?: ReactNode;
}) {
  const desktop = useDesktopPlay();
  const visibleRows = desktop ? (desktopRows ?? rows) : rows;
  return (
    <section className="panel game-details">
      <div className="variant-heading">
        <h2>{ruleset}</h2>
        <span className="badge">
          {size} × {size}
        </span>
      </div>
      {header}
      {header && visibleRows.length > 0 ? <div className="divider" /> : null}
      {visibleRows.map((row) => (
        <div className="detail-row" key={row.label}>
          <span>{row.label}</span>
          <strong>{row.value}</strong>
        </div>
      ))}
      {footer}
    </section>
  );
}
