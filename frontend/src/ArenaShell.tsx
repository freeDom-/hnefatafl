import { useEffect, useRef, type ReactNode } from 'react';
import type { Account } from './Auth';
import SiteHeader from './SiteHeader';
import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import {
  activeDestination,
  useLocationHash,
  workspaceDestinations,
  type Workspace,
} from './workspaceNavigation';
import './arenaShell.css';

const MAIN_ID = 'main-content';

/** One compact primary navigation per workspace; desktop keeps the header. */
function WorkspaceBottomNav({
  workspace,
  current,
  account,
}: {
  workspace: Workspace;
  current?: string;
  account: Account | null;
}) {
  const { t } = useI18n();
  const hash = useLocationHash();
  if (!account) return null;
  const destinations = workspaceDestinations(workspace, account.permissions);
  if (!destinations.length) return null;
  const active = activeDestination(workspace, current, hash);
  return (
    <nav className="workspace-bottom-nav" aria-label={t('Mobile navigation')}>
      {destinations.map((destination) => (
        <a
          key={destination.id}
          href={destination.href}
          onClick={surfaceLink}
          aria-current={active === destination.id ? 'page' : undefined}
        >
          <BottomNavIcon id={destination.id} />
          <span>{t(destination.short)}</span>
        </a>
      ))}
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
      ) : id === 'training' ? (
        <path
          d="M4 5h16v12H4Zm0 16h16M8 9h8m-8 4h5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : id === 'tournaments' ? (
        <path
          d="M7 4h10v3a5 5 0 0 1-10 0Zm5 8v4m-4 4h8m-6-4h4M5 5h2M17 5h2"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : id === 'model-collection' ? (
        <path
          d="m12 3 8 4.5v9L12 21l-8-4.5v-9Zm0 0v18m8-13.5-8 4.5-8-4.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      ) : id === 'workshop-workers' ? (
        <path
          d="M12 3.5 4 8v8l8 4.5 8-4.5V8Zm0 0v17m8-12.5-8 4.5-8-4.5m4-2 8 4.5"
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
      <WorkspaceBottomNav workspace={workspace} current={current} account={account} />
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
