import type { ReactNode } from 'react';
import MobileGameLayout, { type MobileGamePresentation } from './MobileGameLayout';
import { useMediaQuery } from './mobilePlay';

export default function GameLayout({
  sidebar,
  battlefield,
  history,
  mobile,
  switcher,
}: {
  sidebar: ReactNode;
  battlefield: ReactNode;
  history: ReactNode;
  mobile?: MobileGamePresentation;
  switcher?: ReactNode;
}) {
  const compact = useMediaQuery('(max-width: 760px), (max-height: 480px) and (pointer: coarse)');
  if (compact && mobile)
    return (
      <MobileGameLayout
        presentation={mobile}
        battlefield={battlefield}
        history={history}
        switcher={switcher}
      />
    );
  return (
    <>
      {switcher}
      <div className="game-layout">
        <aside className="game-sidebar">{sidebar}</aside>
        {battlefield}
        {history}
      </div>
    </>
  );
}
