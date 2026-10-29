import type { ReactNode } from 'react';
import { useDesktopPlay } from './mobilePlay';

export type GameDetailRow = { label: string; value: ReactNode };

export default function GameDetailsPanel({
  ruleset,
  size,
  header,
  rows,
  footer,
}: {
  ruleset: string;
  size: number;
  header?: ReactNode;
  rows: GameDetailRow[];
  footer?: ReactNode;
}) {
  const desktop = useDesktopPlay();
  return (
    <section className="panel game-details">
      <div className="variant-heading">
        <h2>{ruleset}</h2>
        <span className="badge">
          {size} × {size}
        </span>
      </div>
      {header}
      {header && !desktop ? <div className="divider" /> : null}
      {(!desktop ? rows : []).map((row) => (
        <div className="detail-row" key={row.label}>
          <span>{row.label}</span>
          <strong>{row.value}</strong>
        </div>
      ))}
      {footer}
    </section>
  );
}
