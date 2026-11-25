import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { api, errorMessage } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { notify } from './notifications';
import { useOnlinePoll } from './useOnlinePoll';
import { useProgression } from './Progression';
import type { Profile } from './ProfilePage';
import Portrait from './Portrait';
import type { PortraitCatalog, PortraitState } from './portraitTypes';
import './portraitHall.css';

type HallData = { catalog: PortraitCatalog; profile: Profile };
const labels: Record<PortraitState, string> = {
  locked: 'Locked',
  earned: 'Unlocked',
  selected: 'Selected',
};
const familyRunes: Readonly<Record<string, string>> = {
  'rated-wins': 'ᛏ',
  'brandubh-wins': 'ᛒ',
  'tablut-wins': 'ᛉ',
  'copenhagen-wins': 'ᚲ',
  'defender-wins': 'ᛟ',
  'attacker-wins': 'ᚦ',
  'ai-wins': 'ᚠ',
  'analyzed-replays': 'ᚱ',
  'rated-win-streak': 'ᛋ',
  'local-first-game': 'ᛗ',
};

/** Presentation only: all eligibility, awards and selection come from the server. */
export default function PortraitHall() {
  const { account } = useAuth();
  const { t, locale } = useI18n();
  const [pixelRatio, setPixelRatio] = useState(() => Math.max(1, window.devicePixelRatio || 1));
  useEffect(() => {
    const update = () => setPixelRatio(Math.max(1, window.devicePixelRatio || 1));
    const media = window.matchMedia?.(`(resolution: ${pixelRatio}dppx)`);
    media?.addEventListener('change', update);
    window.addEventListener('resize', update);
    return () => {
      media?.removeEventListener('change', update);
      window.removeEventListener('resize', update);
    };
  }, [pixelRatio]);
  const owner = account?.id ?? '';
  const currentOwner = useRef(owner);
  currentOwner.current = owner;
  const number = useMemo(() => new Intl.NumberFormat(locale), [locale]);
  const progression = useProgression();
  const load = useCallback(async (signal: AbortSignal): Promise<HallData> => {
    const [catalog, profile] = await Promise.all([
      api<PortraitCatalog>('/portraits/catalog', undefined, undefined, signal),
      api<Profile>('/profile', undefined, undefined, signal),
    ]);
    return { catalog, profile };
  }, []);
  const hall = useOnlinePoll(`portrait-hall:${owner}`, load, { intervalMs: 0 });
  const [mutation, setMutation] = useState<{ owner: string; kind: string; error: string } | null>(
    null,
  );
  const pending = useRef<AbortController | null>(null);
  const failedSelection = useRef<{ owner: string; id: string } | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    generation.current++;
    pending.current?.abort();
    pending.current = null;
    failedSelection.current = null;
    setMutation(null);
    return () => {
      generation.current++;
      pending.current?.abort();
      pending.current = null;
    };
  }, [owner]);
  const data = hall.data;
  const busy = mutation?.owner === owner && Boolean(mutation.kind);
  const error = mutation?.owner === owner ? mutation.error : '';
  useEffect(() => {
    const operation = failedSelection.current;
    if (
      !data ||
      hall.phase !== 'ready' ||
      operation?.owner !== owner ||
      data.profile.portrait_id !== operation.id
    )
      return;
    failedSelection.current = null;
    setMutation(null);
    notify.success(t('Portrait selected.'));
    // The recovered operation is confirmed by authoritative data, so dependent
    // consumers receive the same event the normal success path dispatches.
    window.dispatchEvent(new Event('profile-changed'));
  }, [data, hall.phase, owner, t]);

  async function selectPortrait(id: string) {
    if (!data || !owner || pending.current) return;
    const expectedOwner = owner,
      expectedGeneration = generation.current;
    const controller = new AbortController();
    pending.current = controller;
    const current = () =>
      !controller.signal.aborted &&
      currentOwner.current === expectedOwner &&
      generation.current === expectedGeneration;
    setMutation({ owner, kind: id, error: '' });
    hall.hold();
    failedSelection.current = { owner, id };
    try {
      const profile = await api<Profile>(
        '/profile/portrait',
        { portrait_id: id },
        'PATCH',
        controller.signal,
      );
      if (!current()) return;
      failedSelection.current = null;
      hall.accept({ ...data, profile });
      notify.success(t('Portrait selected.'));
      window.dispatchEvent(new Event('profile-changed'));
    } catch (failure) {
      if (current()) setMutation({ owner, kind: '', error: errorMessage(failure) });
    } finally {
      if (current()) {
        pending.current = null;
        setMutation((previous) => ({ owner, kind: '', error: previous?.error ?? '' }));
        void hall.resume();
      }
    }
  }

  if (!account) return null;
  const achievements = progression.achievements;
  // The Hall renders achievement progress from the shared read, so its own
  // loading/stale state covers the profile/catalog read and that shared read.
  const loading = hall.phase === 'loading' || (data !== null && !achievements);
  const stale = hall.phase === 'stale' || progression.phase === 'stale';
  const earned =
    data && achievements
      ? data.catalog.items.filter(
          (item) =>
            item.unlock_achievement_id === null ||
            achievements.unlocked_portrait_ids.includes(item.id),
        ).length
      : 0;
  return (
    <section
      className="portrait-hall"
      style={{ '--portrait-pixel-ratio': pixelRatio } as CSSProperties}
      aria-labelledby="portrait-hall-heading"
      aria-busy={loading}
    >
      <header className="arena-surface portrait-hall-intro">
        <div>
          <p className="arena-kicker">{t('Your saga, your portrait')}</p>
          <h1 id="portrait-hall-heading">{t('Hall of Renown')}</h1>
          <p>
            {t(
              'Earn Viking portraits through your games. Achievements unlock automatically from official results.',
            )}
          </p>
        </div>
        {data && (
          <p className="portrait-hall-count">
            {t('Portraits unlocked: {count} / {total}', {
              count: number.format(earned),
              total: number.format(data.catalog.items.length),
            })}
          </p>
        )}
      </header>
      {loading && <p role="status">{t('Loading your portraits…')}</p>}
      {stale && (
        <div className="form-error portrait-hall-notice" role="alert">
          <p>
            {t('Portrait progress could not be refreshed. Displayed progress may be out of date.')}
          </p>
          <button
            type="button"
            className="button secondary"
            onClick={() => {
              void hall.refresh();
              void progression.refresh();
            }}
          >
            {t('Retry')}
          </button>
        </div>
      )}
      {data && achievements && (
        <>
          {error && (
            <p className="form-error portrait-hall-notice" role="alert">
              {t(error)}
            </p>
          )}
          <ul className="portrait-gallery" aria-label={t('Portrait collection')}>
            {data.catalog.items.map((item) => {
              const achievement = achievements.items.find(
                (entry) => entry.id === item.unlock_achievement_id,
              );
              const unlocked =
                item.unlock_achievement_id === null ||
                achievements.unlocked_portrait_ids.includes(item.id);
              const state: PortraitState =
                data.profile.portrait_id === item.id ? 'selected' : unlocked ? 'earned' : 'locked';
              return (
                <li
                  key={item.id}
                  className={`arena-surface portrait-card portrait-${state}`}
                  data-portrait-state={state}
                >
                  <div className="portrait-card-art">
                    <Portrait id={item.id} gallery />
                  </div>
                  <div className="portrait-card-copy">
                    <span className="portrait-state">{t(labels[state])}</span>
                    <h2>{t(item.name)}</h2>
                    <p>{achievement ? t(achievement.name) : t('Always available.')}</p>
                    {achievement && (
                      <div className="portrait-progress">
                        <label htmlFor={`portrait-progress-${item.id}`}>
                          {t('Progress: {count} / {total}', {
                            count: number.format(achievement.progress),
                            total: number.format(achievement.threshold),
                          })}
                        </label>
                        <progress
                          id={`portrait-progress-${item.id}`}
                          value={Math.min(achievement.progress, achievement.threshold)}
                          max={achievement.threshold}
                        />
                      </div>
                    )}
                    {unlocked && (
                      <button
                        className="button secondary"
                        type="button"
                        disabled={busy || state === 'selected' || hall.phase !== 'ready'}
                        aria-label={t('Select portrait: {name}', { name: t(item.name) })}
                        onClick={() => void selectPortrait(item.id)}
                      >
                        {t(
                          state === 'selected'
                            ? 'Selected'
                            : mutation?.kind === item.id && busy
                              ? 'Selecting…'
                              : 'Use portrait',
                        )}
                      </button>
                    )}
                    {!unlocked && (
                      <p className="portrait-lock-note">
                        {t('Keep playing to complete this achievement.')}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          <section
            className="arena-surface portrait-achievements"
            aria-labelledby="portrait-achievements-heading"
          >
            <h2 id="portrait-achievements-heading">{t('Deeds of Valor')}</h2>
            <p>
              {t(
                'Earned achievements remain unlocked, even if a later result correction changes current progress.',
              )}
            </p>
            <div className="achievement-score">
              <label htmlFor="achievement-score">
                {t('Achievement Score — {earned} / {total} points', {
                  earned: number.format(achievements.summary.earned_points),
                  total: number.format(achievements.summary.total_points),
                })}
              </label>
              <progress
                id="achievement-score"
                aria-describedby="achievement-score-detail"
                value={achievements.summary.earned_points}
                max={achievements.summary.total_points || 1}
              />
              <p id="achievement-score-detail">
                {t('{percent}% complete · {count} / {total} milestones earned', {
                  percent: new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(
                    achievements.summary.total_points
                      ? (achievements.summary.earned_points / achievements.summary.total_points) *
                          100
                      : 0,
                  ),
                  count: number.format(achievements.summary.unlocked),
                  total: number.format(achievements.summary.total),
                })}
              </p>
            </div>
            <ul className="portrait-achievement-list">
              {achievements.families.map((family) => {
                const next = family.next_milestone;
                const state = family.complete
                  ? 'complete'
                  : family.awarded_tier
                    ? 'earned'
                    : 'locked';
                const treatment =
                  family.awarded_tier === family.total_tiers
                    ? 'legendary'
                    : family.awarded_tier / family.total_tiers >= 0.7
                      ? 'gold'
                      : family.awarded_tier / family.total_tiers >= 0.4
                        ? 'silver'
                        : family.awarded_tier
                          ? 'bronze'
                          : 'engraved';
                return (
                  <li
                    key={family.id}
                    className={`achievement-card achievement-${treatment} achievement-${state}`}
                    data-family={family.id}
                    data-tier={family.awarded_tier}
                    data-state={state}
                  >
                    <span className="achievement-rune" aria-hidden="true">
                      {familyRunes[family.id] ?? 'ᛟ'}
                    </span>
                    <h3>{t(family.name)}</h3>
                    <span className="portrait-state">
                      {t(
                        family.complete
                          ? 'Fully completed'
                          : family.awarded_tier
                            ? 'Unlocked'
                            : 'Locked',
                      )}
                    </span>
                    <p>
                      {t('Earned tier: {tier} / {total}', {
                        tier: number.format(family.awarded_tier),
                        total: number.format(family.total_tiers),
                      })}
                    </p>
                    <p>
                      {t('Recorded progress: {progress}', {
                        progress: number.format(family.progress),
                      })}
                    </p>
                    {next && (
                      <>
                        <label htmlFor={`achievement-progress-${family.id}`}>
                          {t('Next milestone: {target} · {points} points', {
                            target: number.format(next.threshold),
                            points: number.format(next.points),
                          })}
                        </label>
                        <progress
                          id={`achievement-progress-${family.id}`}
                          value={Math.min(family.progress, next.threshold)}
                          max={next.threshold}
                        />
                      </>
                    )}
                    <details>
                      <summary>{t('Milestone rewards')}</summary>
                      <ul>
                        {family.milestones.map((milestone) => (
                          <li key={milestone.id}>
                            {number.format(milestone.threshold)} ·{' '}
                            {t('{points} points', { points: number.format(milestone.points) })} ·{' '}
                            {t(milestone.unlocked ? 'Unlocked' : 'Locked')}
                          </li>
                        ))}
                      </ul>
                    </details>
                  </li>
                );
              })}
            </ul>
          </section>
        </>
      )}
    </section>
  );
}
