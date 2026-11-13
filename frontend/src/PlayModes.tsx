import { useState, type ReactNode } from 'react';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import LocalGameDialog from './LocalGameDialog';

/** A running game offered for continuing; every field comes from server data. */
export type PlayModeGame = {
  id: string;
  href: string;
  label: string;
  /** Optional: a partial response must never break the entry list. */
  detail?: string;
  /** True only when the authoritative server state says this account must move. */
  yourTurn: boolean;
};

/**
 * The shared play hierarchy: continue a running game, or start a new one through
 * one of the three equal-status modes. Online creation is the only mode that
 * needs a verified account; local 1v1 and vs-AI play are never rated.
 */
export default function PlayModes({
  onNewOnline,
  onNewSingle,
  games = [],
  children,
}: {
  onNewOnline: () => void;
  onNewSingle?: () => void;
  games?: PlayModeGame[];
  children?: ReactNode;
}) {
  const { account } = useAuth();
  const { t } = useI18n();
  const [localOpen, setLocalOpen] = useState(false);
  const unverified = account?.email_verified === false;
  const waiting = games.filter((game) => game.yourTurn);
  const rest = games.filter((game) => !game.yourTurn);
  const ordered = [...waiting, ...rest];
  return (
    <section className="play-modes" aria-labelledby="play-modes-heading">
      {unverified && (
        <p role="alert" className="dashboard-verification-warning">
          {t('Verify your email address before creating or joining online games.')}{' '}
          <a href="/profile#email-verification" onClick={surfaceLink}>
            {t('Verify your email in your profile.')}
          </a>
        </p>
      )}
      <h2 id="play-modes-heading">{t('Your turn / Continue')}</h2>
      {ordered.length ? (
        <ul className="play-continue">
          {ordered.map((game) => (
            <li key={game.id} className={game.yourTurn ? 'your-turn' : undefined}>
              <a className="button secondary" href={game.href} onClick={surfaceLink}>
                {t('Continue')}
                <span className="sr-only">
                  {': '}
                  {t(game.label)}
                </span>
              </a>
              <span className="play-continue-detail">
                {game.yourTurn && <strong>{t('Your turn')} · </strong>}
                {t(game.label)}
                {game.detail ? ` · ${t(game.detail)}` : ''}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p>{t('No games are currently running.')}</p>
      )}
      {children}
      <div className="play-mode-cards">
        <article className="play-mode-card">
          <h3>{t('Play online')}</h3>
          <p>{t('Rated multiplayer with clocks. Create a game or join the lobby.')}</p>
          <button className="button primary" onClick={onNewOnline} disabled={unverified}>
            {t('New online game')}
          </button>
        </article>
        <article className="play-mode-card">
          <h3>{t('Local 1v1')}</h3>
          <p>{t('Pass and play on one device. Never rated.')}</p>
          <button className="button secondary" onClick={() => setLocalOpen(true)}>
            {t('New local 1v1 game')}
          </button>
        </article>
        <article className="play-mode-card">
          <h3>{t('Play vs AI')}</h3>
          <p>{t('Single-player against a computer opponent. Never rated.')}</p>
          <button className="button secondary" onClick={onNewSingle}>
            {t('New single-player game')}
          </button>
        </article>
      </div>
      {localOpen && <LocalGameDialog onClose={() => setLocalOpen(false)} />}
    </section>
  );
}
