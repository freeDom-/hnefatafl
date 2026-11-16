import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { api, errorMessage } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import { notify } from './notifications';
import { useOnlinePoll } from './useOnlinePoll';
import type { Profile } from './ProfilePage';
import Portrait from './Portrait';
import type { AchievementProgress, PortraitCatalog, PortraitState } from './portraitTypes';
import './portraitHall.css';

type HallData = { catalog: PortraitCatalog; achievements: AchievementProgress; profile: Profile };
const labels: Record<PortraitState, string> = {
  locked: 'Locked',
  available: 'Ready to claim',
  earned: 'Unlocked',
  selected: 'Selected',
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
  const load = useCallback(async (signal: AbortSignal): Promise<HallData> => {
    const [catalog, achievements, profile] = await Promise.all([
      api<PortraitCatalog>('/portraits/catalog', undefined, undefined, signal),
      api<AchievementProgress>('/personal/achievements', undefined, undefined, signal),
      api<Profile>('/profile', undefined, undefined, signal),
    ]);
    return { catalog, achievements, profile };
  }, []);
  const hall = useOnlinePoll(`portrait-hall:${owner}`, load, { intervalMs: 0 });
  const [mutation, setMutation] = useState<{ owner: string; kind: string; error: string } | null>(
    null,
  );
  const pending = useRef<AbortController | null>(null);
  const failedOperation = useRef<{
    owner: string;
    kind: string;
    previous: AchievementProgress;
    eligible: string[];
  } | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    generation.current++;
    pending.current?.abort();
    pending.current = null;
    failedOperation.current = null;
    setMutation(null);
    return () => {
      generation.current++;
      pending.current?.abort();
      pending.current = null;
      failedOperation.current = null;
    };
  }, [owner]);
  const data = hall.data;
  const busy = mutation?.owner === owner && Boolean(mutation.kind);
  const error = mutation?.owner === owner ? mutation.error : '';

  const announceAwards = useCallback(
    (previous: AchievementProgress, next: AchievementProgress) => {
      const oldAwards = new Set(
        previous.items.filter((item) => item.unlocked).map((item) => item.id),
      );
      const added = next.items.filter((item) => item.unlocked && !oldAwards.has(item.id)).length;
      if (added) {
        const points = next.summary.earned_points - previous.summary.earned_points;
        notify.success(
          t('Collected {count} milestones · +{points} points', {
            count: number.format(added),
            points: number.format(points),
          }),
        );
      }
    },
    [t, number],
  );

  // A lost HTTP response can hide a server-side commit. When the authoritative
  // refresh already shows the requested outcome, the earlier mutation error is
  // obsolete; otherwise it stays because the operation genuinely failed. This
  // never invents an award from client state.
  useEffect(() => {
    const operation = failedOperation.current;
    if (!operation || operation.owner !== owner || hall.phase !== 'ready' || !data) return;
    if (operation.kind === 'claim') {
      const applied =
        operation.eligible.length > 0 &&
        operation.eligible.every(
          (id) => data.achievements.items.find((item) => item.id === id)?.unlocked === true,
        );
      if (!applied) return;
      failedOperation.current = null;
      setMutation((previous) =>
        previous?.owner === owner ? { ...previous, error: '' } : previous,
      );
      announceAwards(operation.previous, data.achievements);
      window.dispatchEvent(new Event('profile-changed'));
      return;
    }
    if (data.profile.portrait_id !== operation.kind) return;
    failedOperation.current = null;
    setMutation((previous) => (previous?.owner === owner ? { ...previous, error: '' } : previous));
    notify.success(t('Portrait selected.'));
    window.dispatchEvent(new Event('profile-changed'));
  }, [data, hall.phase, owner, announceAwards, t]);

  async function mutate(kind: 'claim' | string, familyId?: string) {
    if (!data || !owner || pending.current) return;
    const expectedOwner = owner;
    const expectedGeneration = generation.current;
    const controller = new AbortController();
    pending.current = controller;
    const current = () =>
      !controller.signal.aborted &&
      currentOwner.current === expectedOwner &&
      generation.current === expectedGeneration;
    setMutation({ owner, kind, error: '' });
    // Hold the resource while applying a mutation response: an older read must
    // not overwrite confirmed awards or the selected portrait.
    hall.hold();
    // Remember the pre-mutation authority so a later refresh can prove whether a
    // failed request actually committed on the server.
    failedOperation.current = {
      owner,
      kind,
      previous: data.achievements,
      eligible:
        kind === 'claim'
          ? data.achievements.items
              .filter(
                (item) =>
                  !item.unlocked &&
                  item.progress >= item.threshold &&
                  (!familyId || item.family_id === familyId),
              )
              .map((item) => item.id)
          : [],
    };
    try {
      if (kind === 'claim') {
        const achievements = await api<AchievementProgress>(
          '/personal/achievements/confirm',
          familyId ? { family_id: familyId } : {},
          'POST',
          controller.signal,
        );
        if (!current()) return;
        failedOperation.current = null;
        hall.accept({ ...data, achievements });
        announceAwards(data.achievements, achievements);
        window.dispatchEvent(new Event('profile-changed'));
      } else {
        const profile = await api<Profile>(
          '/profile/portrait',
          { portrait_id: kind },
          'PATCH',
          controller.signal,
        );
        if (!current()) return;
        // The PATCH is the mutation: a confirmed selection stays successful even
        // when the follow-up award read fails. Publish it immediately and keep it
        // authoritative against any older in-flight read.
        failedOperation.current = null;
        hall.accept({ ...data, profile });
        notify.success(t('Portrait selected.'));
        window.dispatchEvent(new Event('profile-changed'));
        // Reading the authoritative award list is a separate, recoverable refresh.
        // Its failure must not be reported as a mutation failure; `resume` below
        // re-reads and raises the poll's stale/retry notice only if that also fails.
        try {
          const achievements = await api<AchievementProgress>(
            '/personal/achievements',
            undefined,
            undefined,
            controller.signal,
          );
          if (!current()) return;
          hall.accept({ ...data, profile, achievements });
          announceAwards(data.achievements, achievements);
        } catch {
          // The confirmed selection is already applied; the resume below owns
          // refresh-failure reporting and automatic recovery.
        }
      }
    } catch (failure) {
      if (current()) setMutation({ owner, kind: '', error: errorMessage(failure) });
    } finally {
      if (current()) {
        pending.current = null;
        setMutation((previous) => ({ owner, kind: '', error: previous?.error ?? '' }));
        // Re-read after explicit mutation, including failure (e.g. eligibility
        // changed following a corrected result). No recurring gallery polling.
        void hall.resume();
      }
    }
  }

  if (!account) return null;
  const eligible =
    data?.achievements.items.filter((item) => !item.unlocked && item.progress >= item.threshold)
      .length ?? 0;
  const earned =
    data?.catalog.items.filter(
      (item) =>
        item.unlock_achievement_id === null ||
        data.achievements.items.some(
          (achievement) => achievement.id === item.unlock_achievement_id && achievement.unlocked,
        ),
    ).length ?? 0;
  return (
    <section
      className="portrait-hall"
      style={{ '--portrait-art-max': `${320 / pixelRatio}px` } as CSSProperties}
      aria-labelledby="portrait-hall-heading"
      aria-busy={hall.phase === 'loading'}
    >
      <header className="arena-surface portrait-hall-intro">
        <div>
          <p className="arena-kicker">{t('Your saga, your portrait')}</p>
          <h1 id="portrait-hall-heading">{t('Portrait hall')}</h1>
          <p>
            {t(
              'Earn Viking portraits through your games. Every achievement is confirmed from official results.',
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
      {hall.phase === 'loading' && <p role="status">{t('Loading your portraits…')}</p>}
      {hall.phase === 'stale' && (
        <div className="form-error portrait-hall-notice" role="alert">
          <p>
            {t('Portrait progress could not be refreshed. Displayed progress may be out of date.')}
          </p>
          <button type="button" className="button secondary" onClick={() => void hall.refresh()}>
            {t('Retry')}
          </button>
        </div>
      )}
      {data && (
        <>
          <div className="arena-surface achievement-score">
            <label htmlFor="achievement-score">
              {t('Achievement Score — {earned} / {total} points', {
                earned: number.format(data.achievements.summary.earned_points),
                total: number.format(data.achievements.summary.total_points),
              })}
            </label>
            <progress
              id="achievement-score"
              aria-describedby="achievement-score-detail"
              value={data.achievements.summary.earned_points}
              max={data.achievements.summary.total_points || 1}
            />
            <p id="achievement-score-detail">
              {t('{percent}% complete · {count} / {total} milestones collected', {
                percent: new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(
                  data.achievements.summary.total_points
                    ? (data.achievements.summary.earned_points /
                        data.achievements.summary.total_points) *
                        100
                    : 0,
                ),
                count: number.format(data.achievements.summary.unlocked),
                total: number.format(data.achievements.summary.total),
              })}
            </p>
            <p>
              {t('Ready to collect: {points} points', {
                points: number.format(data.achievements.summary.claimable_points),
              })}
            </p>
          </div>
          <div className="portrait-hall-actions">
            <p>{t('Eligible achievements: {count}', { count: number.format(eligible) })}</p>
            <button
              type="button"
              className="button primary"
              disabled={busy || eligible === 0 || hall.phase !== 'ready'}
              onClick={() => void mutate('claim')}
            >
              {t(
                busy && mutation?.kind === 'claim'
                  ? 'Confirming achievements…'
                  : 'Claim eligible achievements',
              )}
            </button>
          </div>
          {error && (
            <p className="form-error portrait-hall-notice" role="alert">
              {t(error)}
            </p>
          )}
          <ul className="portrait-gallery" aria-label={t('Portrait collection')}>
            {data.catalog.items.map((item) => {
              const achievement = data.achievements.items.find(
                (entry) => entry.id === item.unlock_achievement_id,
              );
              const unlocked =
                item.unlock_achievement_id === null || achievement?.unlocked === true;
              const state: PortraitState =
                data.profile.portrait_id === item.id
                  ? 'selected'
                  : unlocked
                    ? 'earned'
                    : achievement && achievement.progress >= achievement.threshold
                      ? 'available'
                      : 'locked';
              return (
                <li
                  key={item.id}
                  className={`arena-surface portrait-card portrait-${state}`}
                  data-portrait-state={state}
                >
                  <div className="portrait-card-art">
                    <Portrait id={item.id} />
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
                        onClick={() => void mutate(item.id)}
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
                        {t(
                          state === 'available'
                            ? 'Claim eligible achievements to unlock this portrait.'
                            : 'Keep playing to complete this achievement.',
                        )}
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
            <h2 id="portrait-achievements-heading">{t('Achievements')}</h2>
            <p>
              {t(
                'Confirmed achievements remain earned, even if a later result correction changes current progress.',
              )}
            </p>
            <ul className="portrait-achievement-list">
              {data.achievements.families.map((family, index) => {
                const next = family.next_milestone;
                const state = family.complete
                  ? 'complete'
                  : family.claimable_points > 0
                    ? 'claimable'
                    : family.collected_tier
                      ? 'collected'
                      : 'locked';
                const treatment =
                  family.collected_tier === family.total_tiers
                    ? 'legendary'
                    : family.collected_tier / family.total_tiers >= 0.7
                      ? 'gold'
                      : family.collected_tier / family.total_tiers >= 0.4
                        ? 'silver'
                        : family.collected_tier
                          ? 'bronze'
                          : 'engraved';
                return (
                  <li
                    key={family.id}
                    className={`achievement-card achievement-${treatment} achievement-${state}`}
                    data-family={family.id}
                    data-tier={family.collected_tier}
                    data-state={state}
                  >
                    <span className="achievement-rune" aria-hidden="true">
                      {['ᛏ', 'ᛒ', 'ᛉ', 'ᚲ', 'ᛟ', 'ᚦ', 'ᚠ', 'ᚱ', 'ᛋ', 'ᛗ'][index]}
                    </span>
                    <h3>{t(family.name)}</h3>
                    <span className="portrait-state">
                      {t(
                        family.complete
                          ? 'Fully completed'
                          : family.claimable_points > 0
                            ? 'Ready to claim'
                            : family.collected_tier
                              ? 'Unlocked'
                              : 'Locked',
                      )}
                    </span>
                    <p>
                      {t('Collected tier: {tier} / {total} · threshold {threshold}', {
                        tier: number.format(family.collected_tier),
                        total: number.format(family.total_tiers),
                        threshold: number.format(family.collected_threshold),
                      })}
                    </p>
                    <p>
                      {t('Recorded progress: {progress} · highest achieved: {threshold}', {
                        progress: number.format(family.progress),
                        threshold: number.format(family.achieved_threshold),
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
                    {family.claimable_points > 0 && (
                      <button
                        type="button"
                        className="button secondary"
                        disabled={busy || hall.phase !== 'ready'}
                        onClick={() => void mutate('claim', family.id)}
                        aria-label={t('Collect milestones: {name}', { name: t(family.name) })}
                      >
                        {t('Collect · {points} points', {
                          points: number.format(family.claimable_points),
                        })}
                      </button>
                    )}
                    <details>
                      <summary>{t('Milestone rewards')}</summary>
                      <ul>
                        {family.milestones.map((milestone) => (
                          <li key={milestone.id}>
                            {number.format(milestone.threshold)} ·{' '}
                            {t('{points} points', { points: number.format(milestone.points) })} ·{' '}
                            {t(
                              milestone.unlocked
                                ? 'Unlocked'
                                : milestone.progress >= milestone.threshold
                                  ? 'Ready to claim'
                                  : 'Locked',
                            )}
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
