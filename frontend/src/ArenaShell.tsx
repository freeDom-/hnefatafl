import { useEffect, useRef, type ReactNode } from 'react';
import type { Account } from './Auth';
import SiteHeader, { type Workspace } from './SiteHeader';
import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import './arenaShell.css';

const MAIN_ID = 'main-content';

type NavItem = { id: string; label: string; href: string };

/** Mobile bottom navigation; desktop keeps the persistent header navigation. */
function ArenaBottomNav({ current, account }: { current?: string; account: Account | null }) {
  const { t } = useI18n();
  if (!account) return null;
  const items: NavItem[] = [
    { id: 'dashboard', label: 'Home', href: '/dashboard' },
    { id: 'play', label: 'Play', href: '/play' },
    { id: 'statistics', label: 'Stats', href: '/statistics' },
    { id: 'leaderboard', label: 'Compete', href: '/leaderboard' },
    { id: 'archive', label: 'Replays', href: '/archive' },
  ];
  return (
    <nav className="arena-bottom-nav" aria-label={t('Mobile navigation')}>
      {items.map((item) => {
        const active = current === item.id || (item.id === 'play' && current === 'online');
        return (
          <a
            key={item.id}
            href={item.href}
            onClick={surfaceLink}
            aria-current={active ? 'page' : undefined}
          >
            <BottomNavIcon id={item.id} />
            <span>{t(item.label)}</span>
          </a>
        );
      })}
    </nav>
  );
}

function BottomNavIcon({ id }: { id: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      {id === 'dashboard' ? (
        <path
          d="M4 10 12 4l8 6v9a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1Z"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
      ) : id === 'play' ? (
        <>
          <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.7" />
          <path d="m10 8.5 6 3.5-6 3.5Z" fill="currentColor" />
        </>
      ) : id === 'statistics' ? (
        <path
          d="M5 19V11m5 8V5m5 14v-6m5 6V8"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
        />
      ) : id === 'leaderboard' ? (
        <path
          d="M8 4h8v5a4 4 0 0 1-8 0Zm0 0H5v2a3 3 0 0 0 3 3m8-5h3v2a3 3 0 0 1-3 3m-4 3v3m-3 0h6"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      ) : (
        <path
          d="M4 12a8 8 0 1 0 8-8v8Z"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}

function AppShell({
  account,
  logout,
  current,
  workspace,
  children,
}: {
  account: Account | null;
  logout?: () => Promise<void>;
  current?: string;
  workspace: Workspace;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const mounted = useRef(false);
  useEffect(() => {
    document.documentElement.dataset.workspace = workspace;
  }, [workspace]);
  // Move focus to the main region on route changes so keyboard and screen-reader
  // users land in the new view; the initial render keeps its natural focus.
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    document.getElementById(MAIN_ID)?.focus({ preventScroll: true });
  }, [current]);
  return (
    <>
      <a className="skip-link" href={`#${MAIN_ID}`}>
        {t('Skip to main content')}
      </a>
      <SiteHeader account={account} logout={logout} current={current} workspace={workspace} />
      {children}
      <ArenaBottomNav current={current} account={account} />
      <footer className="site-footer">
        <span>{t('ᛟ HNEFATAFL')}</span>
        <p>{t('An ancient game. An open horizon.')}</p>
        <span>{t('Made for thoughtful moves.')}</span>
      </footer>
    </>
  );
}

/** Shared authenticated shell for Arena surfaces (dashboard, play, stats, hall). */
export function ArenaShell(props: Omit<Parameters<typeof AppShell>[0], 'workspace'>) {
  return <AppShell {...props} workspace="arena" />;
}

/** Permission-gated Workshop shell for training, models and AI test tournaments. */
export function WorkshopShell(props: Omit<Parameters<typeof AppShell>[0], 'workspace'>) {
  return <AppShell {...props} workspace="workshop" />;
}

export { MAIN_ID as SHELL_MAIN_ID };
