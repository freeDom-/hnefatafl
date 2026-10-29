import { useId, useState, type ReactNode } from 'react';
import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import Modal from './Modal';
import Tabs from './Tabs';
import { MobilePlayContext } from './mobilePlay';
import './mobilePlay.css';

export type MobileGamePresentation = {
  title: string;
  subtitle: ReactNode;
  players: ReactNode;
  analytics?: ReactNode;
  settings?: ReactNode;
  options?: ReactNode;
  moveCount: number;
};

export default function MobileGameLayout({
  presentation,
  battlefield,
  history,
  switcher,
}: {
  presentation: MobileGamePresentation;
  battlefield: ReactNode;
  history: ReactNode;
  switcher?: ReactNode;
}) {
  const { t } = useI18n();
  const id = useId();
  const [tab, setTab] = useState('moves');
  const [optionsOpen, setOptionsOpen] = useState(false);
  const items = [
    { id: 'moves', label: `${t('Moves')} ${presentation.moveCount}` },
    ...(presentation.analytics !== undefined ? [{ id: 'analytics', label: 'Analytics' }] : []),
    ...(switcher ? [{ id: 'games', label: 'My games' }] : []),
  ];
  return (
    <MobilePlayContext.Provider value>
      <div className="mobile-game-layout">
        <header className="mobile-play-header">
          <a
            className="icon-button"
            href="/dashboard"
            onClick={surfaceLink}
            aria-label={t('Back to dashboard')}
          >
            ‹
          </a>
          <div>
            <h2>{presentation.title}</h2>
            <div className="mobile-play-subtitle">{presentation.subtitle}</div>
          </div>
          {(presentation.options || presentation.settings) && (
            <button
              className="icon-button"
              onClick={() => setOptionsOpen(true)}
              aria-label={t('Game options')}
              aria-haspopup="dialog"
            >
              ⋯
            </button>
          )}
        </header>
        <div className="mobile-player-strip">{presentation.players}</div>
        {battlefield}
        <section className="panel mobile-inspector">
          <Tabs id={id} items={items} active={tab} onChange={setTab} label="Game information" />
          <div
            id={`${id}-panel`}
            role="tabpanel"
            aria-labelledby={`${id}-tab-${tab}`}
            className="mobile-tab-panel"
            tabIndex={0}
          >
            <div className="mobile-tab-content" hidden={tab !== 'moves'}>
              {history}
            </div>
            {presentation.analytics !== undefined && (
              <div className="mobile-tab-content" hidden={tab !== 'analytics'}>
                {presentation.analytics || (
                  <div className="mobile-analysis-empty">
                    <p>{t('Select a move advisor in Game options to see analysis.')}</p>
                    <button className="button secondary" onClick={() => setOptionsOpen(true)}>
                      {t('Move advisor')}
                    </button>
                  </div>
                )}
              </div>
            )}
            <div className="mobile-tab-content" hidden={tab !== 'games'}>
              {tab === 'games' && switcher}
            </div>
          </div>
        </section>
        {optionsOpen && (
          <Modal title={t('Game options')} onClose={() => setOptionsOpen(false)}>
            <div className="mobile-game-options">
              {presentation.options}
              {presentation.settings}
            </div>
          </Modal>
        )}
      </div>
    </MobilePlayContext.Provider>
  );
}
