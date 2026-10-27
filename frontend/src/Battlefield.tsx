import type { ReactNode } from 'react';

export function BoardToolbar({
  leading,
  help,
  trailing,
}: {
  leading?: ReactNode;
  help: string;
  trailing?: ReactNode;
}) {
  return (
    <div className="board-toolbar">
      <div className="button-group">{leading}</div>
      <span className="board-help">{help}</span>
      {trailing}
    </div>
  );
}

export default function Battlefield({
  label,
  board,
  toolbar,
  children,
}: {
  label?: string;
  board: ReactNode;
  toolbar: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="battlefield" aria-label={label}>
      {board}
      {toolbar}
      {children}
    </section>
  );
}
