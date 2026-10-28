import { useI18n } from './i18n';
import { surfaceLink } from './surfaceNavigation';
import type { PersonalStatistics } from './personalTypes';
import {
  badgeTier,
  statisticRuleNames,
  statisticRulesets,
  statisticsHref,
} from './personalStatisticsData';
import './personalStatistics.css';

export default function PerformanceBadges({
  statistics,
  online = false,
}: {
  statistics: PersonalStatistics | undefined;
  online?: boolean;
}) {
  const { t, locale } = useI18n();
  const percent = new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 0 });
  return (
    <div className="performance-badges">
      {statisticRulesets.map((ruleset) => {
        const group = statistics?.groups.find((entry) => entry.ruleset === ruleset);
        const rate = group?.win_rate;
        const tier = online ? 'online' : badgeTier(rate);
        const name = t(statisticRuleNames[ruleset]);
        const elo =
          online &&
          (group?.played ?? 0) > 0 &&
          group?.rating != null &&
          Number.isFinite(group.rating)
            ? Math.round(group.rating)
            : null;
        const label = [
          t('{ruleset} {mode} statistics', {
            ruleset: name,
            mode: t(online ? 'Multiplayer' : 'Singleplayer'),
          }),
          rate == null ? t('No completed games yet.') : percent.format(rate) + ' ' + t('Win rate'),
          elo === null ? null : t('Elo {rating}', { rating: elo }),
        ]
          .filter(Boolean)
          .join(' · ');
        return (
          <a
            key={ruleset}
            href={statisticsHref(online ? 'multiplayer' : 'single-player', ruleset)}
            onClick={surfaceLink}
            className={`performance-badge badge-${tier}`}
            aria-label={label}
          >
            <span className="performance-badge-title">
              <svg className="badge-crest" viewBox="0 0 24 26" aria-hidden="true">
                <path d="M2 2h20v11c0 6-6 10-10 12C8 23 2 19 2 13Z" />
                <path d="m6 9 3 3 3-6 3 6 3-3-2 9H8Z" />
                {(tier === 'gold' || tier === 'royal') && <path d="M5 4h14M5 21l-3-3m17 3 3-3" />}
                {tier === 'royal' && <path d="m12 0 1 2-1 2-1-2Z" />}
              </svg>
              {name}
            </span>
            <strong>{rate == null ? '—' : percent.format(rate)}</strong>
            <span className="sr-only">{t('Win rate')}</span>
            {elo !== null && <small>{t('Elo {rating}', { rating: elo })}</small>}
          </a>
        );
      })}
    </div>
  );
}
