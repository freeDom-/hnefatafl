import type { Account } from './Auth';
import AccountMenu from './AccountMenu';
import { LanguageSelect, useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import ThemeSelect from './ThemeSelect';
import type { Workspace } from './workspaceNavigation';
import './arenaTopBar.css';

/** Slim content header above the desktop rail's main column: the app context,
 * the theme picker, the language selector and the account menu. The rail itself
 * carries navigation and identity, so this bar holds no primary navigation. */
export default function ArenaTopBar({
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
  return (
    <header className="arena-topbar" data-workspace={workspace}>
      <a className="arena-topbar-brand" href="/dashboard" onClick={surfaceLink}>
        <span className="arena-topbar-wordmark">{t('HNEFATAFL')}</span>
        <small>{t('THE WAR TABLE')}</small>
      </a>
      <span className="arena-topbar-band" aria-hidden="true" />
      <div className="arena-topbar-controls">
        <ThemeSelect />
        <LanguageSelect />
        <span className="arena-topbar-workspace">
          {workspace === 'workshop' ? t('AUTHORIZED WORKSPACE') : t('THE ARENA')}
        </span>
        <AccountMenu key={current} username={account.username} logout={logout} />
      </div>
    </header>
  );
}
