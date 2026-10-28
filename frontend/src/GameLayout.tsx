import type { ReactNode } from 'react';

export default function GameLayout({
  sidebar,
  battlefield,
  history,
}: {
  sidebar: ReactNode;
  battlefield: ReactNode;
  history: ReactNode;
}) {
  return (
    <div className="game-layout">
      <aside className="game-sidebar">{sidebar}</aside>
      {battlefield}
      {history}
    </div>
  );
}
