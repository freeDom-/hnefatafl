import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
      if (added)
        notify.success(t('Achievements unlocked: {count}', { count: number.format(added) }));
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

  async function mutate(kind: 'claim' | string) {
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
              .filter((item) => !item.unlocked && item.progress >= item.threshold)
              .map((item) => item.id)
          : [],
    };
    try {
      if (kind === 'claim') {
        const achievements = await api<AchievementProgress>(
          '/personal/achievements/confirm',
          {},
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
                    <p>{achievement ? t(achievement.description) : t('Always available.')}</p>
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
              {data.achievements.items.map((item) => (
                <li key={item.id}>
                  <div>
                    <h3>{t(item.name)}</h3>
                    <p>{t(item.description)}</p>
                  </div>
                  <span className="portrait-state">
                    {t(
                      item.unlocked
                        ? 'Unlocked'
                        : item.progress >= item.threshold
                          ? 'Ready to claim'
                          : 'Locked',
                    )}
                  </span>
                  <label htmlFor={`achievement-progress-${item.id}`}>
                    {t('Progress: {count} / {total}', {
                      count: number.format(item.progress),
                      total: number.format(item.threshold),
                    })}
                  </label>
                  <progress
                    id={`achievement-progress-${item.id}`}
                    value={Math.min(item.progress, item.threshold)}
                    max={item.threshold}
                  />
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </section>
  );
}
