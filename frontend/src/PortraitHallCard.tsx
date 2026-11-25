import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { api } from './api';
import { useI18n } from './i18n';
import { dismissNotification, notify } from './notifications';
import Portrait from './Portrait';
import { useProgression } from './Progression';
import { CELEBRATION_DURATION_MS, playPortraitReveal } from './portraitReveal';
import type { Achievement, PortraitCatalog, PortraitState, PortraitUnlock } from './portraitTypes';

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';
const UNLOCK_REQUEST_TIMEOUT_MS = 15000;
const labels = {
  locked: 'Locked',
  ready: 'Ready to unlock',
  unlocking: 'Unlocking…',
  earned: 'Unlocked',
  selected: 'Selected',
};
const sparks = [
  ['ᛟ', '4%', '28%'],
  ['ᛉ', '96%', '24%'],
  ['✦', '29%', '7%'],
  ['ᚱ', '35%', '96%'],
  ['✧', '6%', '61%'],
  ['ᛟ', '96%', '57%'],
  ['ᛉ', '66%', '8%'],
  ['✦', '77%', '96%'],
  ['ᚱ', '7%', '84%'],
  ['✧', '94%', '86%'],
] as const;

export default function PortraitHallCard({
  item,
  achievement,
  state,
  number,
  selecting,
  selectionDisabled,
  onSelect,
}: {
  item: PortraitCatalog['items'][number];
  achievement: Achievement | undefined;
  state: PortraitState;
  number: Intl.NumberFormat;
  selecting: boolean;
  selectionDisabled: boolean;
  onSelect: (id: string) => void;
}) {
  const { t } = useI18n();
  const { confirmPortraitUnlock, refresh } = useProgression();
  const request = useRef<AbortController | null>(null);
  const image = useRef<HTMLImageElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [unlocking, setUnlocking] = useState(false);
  const [animation, setAnimation] = useState<'reveal' | 'celebrate' | null>(null);
  const displayState = unlocking ? 'unlocking' : state;
  const canEquip = displayState === 'earned' || displayState === 'selected';
  const name = t(item.name);

  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (!animation) return;
    const motion = window.matchMedia?.(REDUCED_MOTION);
    const finish = () => setAnimation(null);
    const interrupt = () => {
      if (motion?.matches || document.visibilityState === 'hidden') finish();
    };
    if (motion?.matches || document.visibilityState === 'hidden') {
      finish();
      return;
    }
    motion?.addEventListener('change', interrupt);
    document.addEventListener('visibilitychange', interrupt);
    let dispose: (() => void) | undefined;
    if (animation === 'reveal') {
      dispose = playPortraitReveal(
        image.current,
        canvas.current,
        () => setAnimation('celebrate'),
        finish,
      );
    } else {
      const timeout = window.setTimeout(finish, CELEBRATION_DURATION_MS);
      dispose = () => window.clearTimeout(timeout);
    }
    return () => {
      dispose?.();
      motion?.removeEventListener('change', interrupt);
      document.removeEventListener('visibilitychange', interrupt);
    };
  }, [animation]);

  async function unlock() {
    if (state !== 'ready' || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setUnlocking(true);
    try {
      const result = await api<PortraitUnlock>(
        '/portraits/unlock',
        { portrait_id: item.id },
        'POST',
        AbortSignal.any([controller.signal, AbortSignal.timeout(UNLOCK_REQUEST_TIMEOUT_MS)]),
      );
      if (controller.signal.aborted) return;
      if (
        result.newly_unlocked &&
        !window.matchMedia?.(REDUCED_MOTION)?.matches &&
        document.visibilityState !== 'hidden'
      ) {
        setAnimation('reveal');
      }
      confirmPortraitUnlock(result.portrait_id);
    } catch {
      if (!controller.signal.aborted) {
        const notice = notify.error(t('Could not unlock {name}. Please try again.', { name }), {
          timeout: 6000,
        });
        // The server may have committed the claim before the response was lost.
        void refresh().then((progress) => {
          if (progress?.unlocked_portrait_ids?.includes(item.id)) dismissNotification(notice);
        });
      }
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setUnlocking(false);
      }
    }
  }

  return (
    <li
      className={`arena-surface portrait-card portrait-${displayState}`}
      data-portrait-state={displayState}
    >
      <div className="portrait-card-frame">
        <div className={`portrait-card-art${animation === 'reveal' ? ' portrait-revealing' : ''}`}>
          <Portrait id={item.id} gallery imageRef={image} />
          {animation === 'reveal' && (
            <canvas ref={canvas} className="portrait-reveal-canvas" aria-hidden="true" />
          )}
        </div>
        {animation === 'celebrate' && (
          <div
            className="portrait-unlock-celebration"
            aria-hidden="true"
            style={{ '--celebration-duration': `${CELEBRATION_DURATION_MS}ms` } as CSSProperties}
          >
            {sparks.map(([glyph, left, top], index) => (
              <span
                key={left + top}
                style={{ left, top, '--rune-delay': `${(index % 3) * 65}ms` } as CSSProperties}
              >
                {glyph}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="portrait-card-copy">
        <span className="portrait-state">{t(labels[displayState])}</span>
        <h2>{name}</h2>
        <p>{achievement ? t(achievement.name) : t('Always available.')}</p>
        <div className="portrait-card-action">
          {displayState === 'locked' ? (
            <>
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
              <p className="portrait-lock-note">
                {t('Keep playing to complete this achievement.')}
              </p>
            </>
          ) : (
            <button
              className={`button ${canEquip ? 'secondary' : 'primary'}`}
              type="button"
              disabled={canEquip && (selectionDisabled || state === 'selected')}
              aria-disabled={unlocking || undefined}
              aria-label={t(canEquip ? 'Select portrait: {name}' : 'Unlock portrait: {name}', {
                name,
              })}
              onClick={canEquip ? () => onSelect(item.id) : () => void unlock()}
            >
              {t(
                canEquip
                  ? state === 'selected'
                    ? 'Selected'
                    : selecting
                      ? 'Selecting…'
                      : 'Use portrait'
                  : unlocking
                    ? 'Unlocking…'
                    : 'Unlock portrait',
              )}
            </button>
          )}
        </div>
      </div>
    </li>
  );
}
