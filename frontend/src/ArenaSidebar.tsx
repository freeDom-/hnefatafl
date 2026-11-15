import { useId, useState } from 'react';
import type { Account } from './Auth';
import { useAppearance } from './Appearance';
import { UI_THEMES, resolveUiTheme } from './appearanceConfig';
import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import { ARENA_SIDEBAR_ITEMS, workshopDestinationId, type SidebarItem } from './sidebarNavigation';
import { DestinationIcon } from './workspaceIcons';
import {
  activeDestination,
  useLocationHash,
  workshopEntry,
  workspaceDestinations,
  type Workspace,
} from './workspaceNavigation';
import './arenaSidebar.css';

const KNOT_ICON = '/assets/arena-v2/assets/icons/hnefatafl-knot.svg';

function themeLabel(theme: string) {
  return UI_THEMES.find((candidate) => candidate.id === theme)?.name ?? theme;
}

/** Full-height desktop rail: brand, the Arena destinations, the permission-gated
 * Workshop section and the account panel. The compact header plus bottom bar keep
 * the same destinations below the desktop breakpoint, so exactly one primary
 * navigation is visible per viewport. */
export default function ArenaSidebar({
  account,
  current,
  workspace = 'arena',
}: {
  account: Account | null;
  logout?: () => Promise<void>;
  current?: string;
  workspace?: Workspace;
}) {
  const { t } = useI18n();
  const { appearance } = useAppearance();
  const hash = useLocationHash();
  if (!account) return null;
  const active =
    activeDestination('arena', current, hash) ?? activeDestination('workshop', current, hash);
  const entry = workshopEntry(account.permissions);
  const entryId = workshopDestinationId(entry);
  const workshopItems = workspaceDestinations('workshop', account.permissions);
  const resolvedTheme = themeLabel(resolveUiTheme(appearance));
  return (
    <aside className="arena-sidebar" data-workspace={workspace}>
      <a className="sidebar-brand" href="/dashboard" onClick={surfaceLink}>
        <span className="sidebar-brand-emblem">
          <img src={KNOT_ICON} alt="" width="34" height="34" />
        </span>
        <span className="sidebar-brand-text">
          {t('HNEFATAFL')}
          <small>{t(resolvedTheme)}</small>
        </span>
      </a>
      <span className="sidebar-band" aria-hidden="true" />
      <nav
        className="sidebar-nav"
        aria-label={workspace === 'workshop' ? t('Workshop navigation') : t('Main navigation')}
      >
        <p className="sidebar-group-label">{t('THE ARENA')}</p>
        {ARENA_SIDEBAR_ITEMS.map((item) => (
          <SidebarEntry key={item.id} item={item} active={active} />
        ))}
        {workshopItems.length > 0 && (
          <>
            <span className="sidebar-divider" role="separator" />
            <p className="sidebar-group-label">{t('AUTHORIZED WORKSPACE')}</p>
            <a href={entry ?? workshopItems[0].href} onClick={surfaceLink}>
              <DestinationIcon id={entryId ?? 'training'} />
              <span>{t('AI Workshop')}</span>
              <span className="sidebar-external" aria-hidden="true">
                ↗
              </span>
            </a>
            <div className="sidebar-sublist">
              {workshopItems.map((destination) => (
                <a
                  key={destination.id}
                  href={destination.href}
                  data-destination={destination.id}
                  onClick={surfaceLink}
                  aria-current={active === destination.id ? 'page' : undefined}
                >
                  <DestinationIcon id={destination.id} />
                  <span>{t(destination.label)}</span>
                </a>
              ))}
            </div>
          </>
        )}
      </nav>
      <div className="sidebar-account">
        <span className="sidebar-account-avatar" aria-hidden="true">
          {account.username.slice(0, 1).toUpperCase()}
        </span>
        <span className="sidebar-account-identity">
          <strong>{account.username}</strong>
          <small>{workspace === 'workshop' ? t('AI Workshop') : t('THE ARENA')}</small>
        </span>
      </div>
    </aside>
  );
}

function SidebarEntry({ item, active }: { item: SidebarItem; active?: string }) {
  const { t } = useI18n();
  if (item.kind === 'destination')
    return (
      <a
        href={item.href}
        data-destination={item.id}
        onClick={surfaceLink}
        aria-current={active === item.id ? 'page' : undefined}
      >
        <DestinationIcon id={item.id} />
        <span>{t(item.label)}</span>
      </a>
    );
  if (item.kind === 'placeholder')
    return (
      <span className="sidebar-nav-placeholder" aria-disabled="true">
        <DestinationIcon id={item.id} />
        <span>{t(item.label)}</span>
        <small className="sidebar-soon">{t('Soon')}</small>
      </span>
    );
  return <SidebarGroup item={item} active={active} />;
}

/** An expandable destination group; it starts expanded so every child stays one
 * click away and the deep-linked section anchors remain reachable. */
function SidebarGroup({
  item,
  active,
}: {
  item: Extract<SidebarItem, { kind: 'group' }>;
  active?: string;
}) {
  const { t } = useI18n();
  const id = useId();
  const [open, setOpen] = useState(true);
  return (
    <div className="sidebar-group">
      <button
        type="button"
        className="sidebar-group-toggle"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        <DestinationIcon id={item.id} />
        <span>{t(item.label)}</span>
        <span className="sidebar-chevron" aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
      </button>
      {open && (
        <div className="sidebar-sublist" id={id}>
          {item.children.map((child) => (
            <SidebarEntry key={child.id} item={child} active={active} />
          ))}
        </div>
      )}
    </div>
  );
}
