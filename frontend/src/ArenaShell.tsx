import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { Account } from './Auth';
import ArenaSidebar from './ArenaSidebar';
import ArenaTopBar from './ArenaTopBar';
import SiteHeader from './SiteHeader';
import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import { DestinationIcon } from './workspaceIcons';
import {
  activeDestination,
  useLocationHash,
  workspaceDestinations,
  type Workspace,
} from './workspaceNavigation';
import './arenaShell.css';

const MAIN_ID = 'main-content';

/** Wide viewports carry the persistent rail instead of the compact top bar. The
 * compact shell's own breakpoint (see `siteHeader.css` / `arenaShell.css`) wins
 * for short landscape devices with a coarse pointer. */
const DESKTOP_SIDEBAR_QUERY = '(min-width: 1024px)';
const COMPACT_LANDSCAPE_QUERY = '(max-height: 480px) and (pointer: coarse)';

function readDesktopSidebar() {
  if (typeof window.matchMedia !== 'function') return false;
  return (
    window.matchMedia(DESKTOP_SIDEBAR_QUERY).matches &&
    !window.matchMedia(COMPACT_LANDSCAPE_QUERY).matches
  );
}

function useDesktopSidebar() {
  const [desktop, setDesktop] = useState(readDesktopSidebar);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const wide = window.matchMedia(DESKTOP_SIDEBAR_QUERY);
    const short = window.matchMedia(COMPACT_LANDSCAPE_QUERY);
    const update = () => setDesktop(wide.matches && !short.matches);
    update();
    wide.addEventListener('change', update);
    short.addEventListener('change', update);
    return () => {
      wide.removeEventListener('change', update);
      short.removeEventListener('change', update);
    };
  }, []);
  return desktop;
}

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
          data-destination={destination.id}
          onClick={surfaceLink}
          aria-current={active === destination.id ? 'page' : undefined}
        >
          <DestinationIcon id={destination.id} />
          <span>{t(destination.short)}</span>
        </a>
      ))}
    </nav>
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
  const desktopSidebar = useDesktopSidebar();
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
      {desktopSidebar ? (
        <>
          <ArenaSidebar account={account} logout={logout} current={current} workspace={workspace} />
          <ArenaTopBar account={account} logout={logout} current={current} workspace={workspace} />
        </>
      ) : (
        <SiteHeader account={account} logout={logout} current={current} workspace={workspace} />
      )}
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
