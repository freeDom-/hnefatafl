import type { ReactNode } from 'react';
import MobileGameLayout, { type MobileGamePresentation } from './MobileGameLayout';
import { DesktopPlayContext, useMediaQuery } from './mobilePlay';
import DesktopInspector from './DesktopInspector';
import './desktopPlay.css';

export default function GameLayout({
  sidebar,
  battlefield,
  history,
  mobile,
  switcher,
  desktop,
}: {
  sidebar: ReactNode;
  battlefield: ReactNode;
  history: ReactNode;
  mobile?: MobileGamePresentation;
  switcher?: ReactNode;
  desktop?: { sidebar: ReactNode; analytics?: ReactNode; moveCount: number };
}) {
  const compact = useMediaQuery('(max-width: 760px), (max-height: 480px) and (pointer: coarse)');
  const desktopViewport = useMediaQuery('(min-width: 1101px)');
  const tabbed = desktopViewport && !!desktop;
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
    <DesktopPlayContext.Provider value={tabbed}>
      {!tabbed && switcher}
      <div className={`game-layout${tabbed ? ' desktop-game-layout' : ''}`}>
        <aside className="game-sidebar">{tabbed ? desktop.sidebar : sidebar}</aside>
        {battlefield}
        {tabbed ? (
          <DesktopInspector
            history={history}
            analytics={desktop.analytics}
            moveCount={desktop.moveCount}
            switcher={switcher}
          />
        ) : (
          history
        )}
      </div>
    </DesktopPlayContext.Provider>
  );
}
