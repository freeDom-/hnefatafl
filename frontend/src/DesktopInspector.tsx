import { useId, type ReactNode } from 'react';
import Tabs from './Tabs';
import { useI18n } from './i18n';
import { usePlayTab } from './PlayTabs';

/** Each tab owns one scrolling surface; the surrounding panel never scrolls. */
export default function DesktopInspector({
  history,
  analytics,
  switcher,
  moveCount,
}: {
  history: ReactNode;
  analytics?: ReactNode;
  switcher?: ReactNode;
  moveCount: number;
}) {
  const id = useId();
  const { t } = useI18n();
  const items = [
    { id: 'moves', label: `${t('Moves')} ${moveCount}` },
    ...(analytics !== undefined ? [{ id: 'analytics', label: 'Analytics' }] : []),
    ...(switcher ? [{ id: 'games', label: 'My games' }] : []),
  ];
  const [tab, setTab] = usePlayTab(items.map((item) => item.id));
  return (
    <section className="panel desktop-inspector">
      <Tabs id={id} items={items} active={tab} onChange={setTab} label="Game information" />
      <div
        id={`${id}-panel`}
        role="tabpanel"
        aria-labelledby={`${id}-tab-${tab}`}
        className="desktop-tab-panel"
        tabIndex={0}
      >
        <div className="desktop-tab-content desktop-moves" hidden={tab !== 'moves'}>
          {history}
        </div>
        {analytics !== undefined && (
          <div className="desktop-tab-content" hidden={tab !== 'analytics'}>
            {analytics}
          </div>
        )}
        <div className="desktop-tab-content desktop-games" hidden={tab !== 'games'}>
          {tab === 'games' && switcher}
        </div>
      </div>
    </section>
  );
}
