import type { Account } from './Auth';
import AccountMenu from './AccountMenu';
import { Crown } from './Board';
import { LanguageSelect, useI18n } from './i18n';
import { navigate } from './navigation';
import { surfaceLink } from './surfaceNavigation';
import {
  activeDestination,
  useLocationHash,
  workshopEntry,
  workspaceDestinations,
  type Workspace,
} from './workspaceNavigation';
import './siteHeader.css';

export default function SiteHeader({
  account,
  logout,
  current,
  workspace = 'arena',
}: {
  account: Account | null;
  logout?: () => Promise<void>;
  current?: string;
  workspace?: Workspace;
}) {
  const { t } = useI18n();
  const hash = useLocationHash();
  if (!account) return null;
  const destinations = workspaceDestinations(workspace, account.permissions);
  const active = activeDestination(workspace, current, hash);
  const entry = workshopEntry(account.permissions);
  const label = workspace === 'workshop' ? t('Arena') : t('Workshop');
  return (
    <header className="site-header" data-workspace={workspace}>
      <a className="brand" href="/dashboard" onClick={surfaceLink}>
        <span className="brand-emblem">
          <Crown />
        </span>
        <span>
          {t('HNEFATAFL')}
          <small>{t('THE KING’S TABLE')}</small>
        </span>
      </a>
      {destinations.length > 0 && (
        <nav
          aria-label={workspace === 'workshop' ? t('Workshop navigation') : t('Main navigation')}
        >
          {destinations.map((destination) => (
            <button
              type="button"
              key={destination.id}
              data-destination={destination.id}
              onClick={() => navigate(destination.href)}
              className="site-nav-link"
              aria-current={active === destination.id ? 'page' : undefined}
            >
              {t(destination.label)}
            </button>
          ))}
        </nav>
      )}
      <div className="header-controls">
        {entry && (
          <button
            type="button"
            className="workspace-switch"
            aria-pressed={workspace === 'workshop'}
            aria-label={label}
            title={label}
            onClick={() => navigate(workspace === 'workshop' ? '/dashboard' : entry)}
          >
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              {workspace === 'workshop' ? (
                <>
                  <path
                    d="M4 10 12 4l8 6v9a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1Z"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinejoin="round"
                  />
                  <path d="M9 20v-5h6v5" stroke="currentColor" strokeWidth="1.6" />
                </>
              ) : (
                <>
                  <path
                    d="M14.5 5.5a3.5 3.5 0 0 0 4.2 4.6L21 12.4 12.4 21l-2.3-2.3a3.5 3.5 0 0 1-4.6-4.2L3 12l8.6-8.6Z"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinejoin="round"
                  />
                  <path d="m7 17 2 2m2-6 2 2" stroke="currentColor" strokeWidth="1.6" />
                </>
              )}
            </svg>
          </button>
        )}
        <LanguageSelect />
        <AccountMenu key={current} username={account.username} logout={logout} />
      </div>
    </header>
  );
}
