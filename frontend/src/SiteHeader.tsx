import type { Account } from './Auth';
import AccountMenu from './AccountMenu';
import { Crown } from './Board';
import { LanguageSelect, useI18n } from './i18n';
import { navigate } from './navigation';
import { surfaceLink } from './surfaceNavigation';
import './siteHeader.css';

export type Workspace = 'arena' | 'workshop';

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
  if (!account) return null;
  const mayEnterWorkshop = account.permissions.some(
    (permission) => permission === 'training' || permission === 'tournaments',
  );
  return (
    <header className="site-header">
      <a className="brand" href="/dashboard" onClick={surfaceLink}>
        <span className="brand-emblem">
          <Crown />
        </span>
        <span>
          {t('HNEFATAFL')}
          <small>{t('THE KING’S TABLE')}</small>
        </span>
      </a>
      <nav aria-label={t('Main navigation')}>
        {(
          [
            ['dashboard', 'Dashboard'],
            ['play', 'Play'],
            ['statistics', 'Statistics'],
            ['leaderboard', 'Leaderboard'],
            ['archive', 'Hall of records'],
            ['training', 'AI workshop'],
            ['tournaments', 'Tournaments'],
          ] as const
        )
          .filter(
            ([item]) =>
              (item !== 'training' && item !== 'tournaments') || account.permissions.includes(item),
          )
          .map(([item, label]) => (
            <button
              type="button"
              key={item}
              onClick={() => navigate(`/${item}`)}
              className="site-nav-link"
              aria-current={current === item ? 'page' : undefined}
            >
              {t(label)}
            </button>
          ))}
      </nav>
      <div className="header-controls">
        {mayEnterWorkshop && (
          <button
            type="button"
            className="workspace-switch"
            aria-pressed={workspace === 'workshop'}
            aria-label={workspace === 'workshop' ? t('Arena') : t('Workshop')}
            title={workspace === 'workshop' ? t('Arena') : t('Workshop')}
            onClick={() => navigate(workspace === 'workshop' ? '/dashboard' : '/training')}
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
