import type { ReactNode } from 'react';

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
  rows: { label: string; value: ReactNode }[];
  footer?: ReactNode;
}) {
  return (
    <section className="panel game-details">
      <div className="variant-heading">
        <h2>{ruleset}</h2>
        <span className="badge">
          {size} × {size}
        </span>
      </div>
      {header}
      {header ? <div className="divider" /> : null}
      {rows.map((row) => (
        <div className="detail-row" key={row.label}>
          <span>{row.label}</span>
          <strong>{row.value}</strong>
        </div>
      ))}
      {footer}
    </section>
  );
}
