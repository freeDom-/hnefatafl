import type { Account } from './Auth';
import { AppearanceSettings } from './Appearance';
import { Crown } from './Board';
import { LanguageSelect, useI18n } from './i18n';
import { navigate } from './navigation';
import { surfaceLink } from './surfaceNavigation';

export default function SiteHeader({
  account,
  logout,
  current,
}: {
  account: Account | null;
  logout?: () => Promise<void>;
  current?: string;
}) {
  const { t } = useI18n();
  if (!account) return null;
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
              className={current === item ? 'active' : ''}
              aria-current={current === item ? 'page' : undefined}
            >
              {t(label)}
            </button>
          ))}
      </nav>
      <div className="header-controls">
        <span className="account-name" title={account.username}>
          {account.username}
        </span>
        <button className="button small secondary" type="button" onClick={() => void logout?.()}>
          {t('Log out')}
        </button>
        <LanguageSelect />
        <AppearanceSettings />
      </div>
    </header>
  );
}
