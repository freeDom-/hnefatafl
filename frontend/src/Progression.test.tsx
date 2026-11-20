import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ProgressionProvider, useProgression } from './Progression';
import { PROGRESSION_POLL_MS } from './useAchievementNotifications';
import { api } from './api';
import { notify } from './notifications';
import type { AchievementProgress } from './portraitTypes';

vi.mock('./api', () => ({ api: vi.fn() }));
vi.mock('./notifications', () => ({ notify: { success: vi.fn() } }));

function snapshot(earnedPoints = 0, unlocked = false): AchievementProgress {
  return {
    catalog_version: 3,
    portrait_catalog_version: 2,
    as_of: '2035-01-01T00:00:00Z',
    families: [],
    unlocked_portrait_ids: unlocked ? ['shieldmaiden'] : [],
    summary: {
      unlocked: unlocked ? 1 : 0,
      total: 77,
      earned_points: earnedPoints,
      total_points: 6290,
    },
    items: unlocked
      ? [
          {
            id: 'rated-wins-10',
            family_id: 'rated-wins',
            tier: 3,
            points: 30,
            name: 'Rated online victories',
            description: '',
            category: 'rated',
            scope: null,
            threshold: 10,
            progress: 10,
            unlocked: true,
            awarded_at: '2035-01-01T00:00:00Z',
            portrait_id: 'shieldmaiden',
          },
        ]
      : [],
  };
}

function Probe() {
  const { achievements, phase } = useProgression();
  return (
    <span data-testid="probe">
      {phase}:{achievements?.summary.earned_points ?? 'none'}
    </span>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it('preserves shared achievement data across Great Hall/Hall of Renown cadence changes', async () => {
  vi.useFakeTimers();
  vi.mocked(api).mockResolvedValue(snapshot(30, true) as never);
  const view = render(
    <ProgressionProvider owner="archer" intervalMs={0}>
      <Probe />
    </ProgressionProvider>,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByTestId('probe')).toHaveTextContent('ready:30');
  expect(api).toHaveBeenCalledTimes(1);

  // Enter Hall of Renown: the cadence turns on but the snapshot must survive.
  view.rerender(
    <ProgressionProvider owner="archer" intervalMs={PROGRESSION_POLL_MS}>
      <Probe />
    </ProgressionProvider>,
  );
  expect(screen.getByTestId('probe')).toHaveTextContent('ready:30');
  expect(api).toHaveBeenCalledTimes(1);

  // Return to the Great Hall: turning the cadence off does not reload either.
  view.rerender(
    <ProgressionProvider owner="archer" intervalMs={0}>
      <Probe />
    </ProgressionProvider>,
  );
  expect(screen.getByTestId('probe')).toHaveTextContent('ready:30');
  expect(api).toHaveBeenCalledTimes(1);
  expect(notify.success).not.toHaveBeenCalled();
});

it('announces one new award batch after a cadence change and never repeats history', async () => {
  vi.useFakeTimers();
  let payload = snapshot();
  vi.mocked(api).mockImplementation(async () => structuredClone(payload) as never);
  const view = render(
    <ProgressionProvider owner="archer" intervalMs={0}>
      <Probe />
    </ProgressionProvider>,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByTestId('probe')).toHaveTextContent('ready:0');
  expect(notify.success).not.toHaveBeenCalled();

  view.rerender(
    <ProgressionProvider owner="archer" intervalMs={PROGRESSION_POLL_MS}>
      <Probe />
    </ProgressionProvider>,
  );
  // The backstop coincides with a remote completion.
  payload = snapshot(30, true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(PROGRESSION_POLL_MS);
  });
  expect(screen.getByTestId('probe')).toHaveTextContent('ready:30');
  expect(notify.success).toHaveBeenCalledWith('New milestones: 1 · +30 points');
  expect(notify.success).toHaveBeenCalledWith('New portraits available: 1');
  expect(notify.success).toHaveBeenCalledTimes(2);

  // A later poll of the same historical awards must not announce them again.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(PROGRESSION_POLL_MS);
  });
  expect(notify.success).toHaveBeenCalledTimes(2);
});

it('isolates a replacement account while a cadence change is pending', async () => {
  vi.useFakeTimers();
  let finish: (value: AchievementProgress) => void = () => {};
  let signal: AbortSignal | undefined;
  vi.mocked(api).mockImplementation((_path, _body, _method, requestSignal) => {
    signal = requestSignal;
    return new Promise((resolve) => {
      finish = resolve as (value: AchievementProgress) => void;
    });
  });
  const view = render(
    <ProgressionProvider owner="archer" intervalMs={0}>
      <Probe />
    </ProgressionProvider>,
  );
  expect(signal?.aborted).toBe(false);
  const oldSignal = signal;
  const oldFinish = finish;
  view.rerender(
    <ProgressionProvider owner="keeper" intervalMs={PROGRESSION_POLL_MS}>
      <Probe />
    </ProgressionProvider>,
  );
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => {
    oldFinish(snapshot(30, true));
  });
  expect(screen.getByTestId('probe')).toHaveTextContent('loading:none');
  expect(notify.success).not.toHaveBeenCalled();
});
