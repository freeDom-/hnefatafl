import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import TrainingLiveStatus, { LiveElapsed } from './TrainingLiveStatus';
import { setLanguage } from './i18n';
import { formatDuration, phaseSummary, useLiveProgress } from './trainingLiveProgress';
import type { Job } from './types';

function runningJob(progress: Record<string, unknown>): Job {
  return { id: 'job-1', status: 'running', progress };
}

/** Renders the live timings the job header displays. */
function Timings({ job }: { job: Job }) {
  const live = useLiveProgress(job);
  return (
    <div>
      <span data-testid="elapsed">
        {live.elapsed === undefined ? '—' : formatDuration(live.elapsed)}
      </span>
      <span data-testid="phase">
        {live.phaseElapsed === undefined ? '—' : formatDuration(live.phaseElapsed)}
      </span>
      <span data-testid="age">
        {live.progressAge === undefined ? '—' : Math.floor(live.progressAge)}
      </span>
      <span data-testid="heartbeat">{live.heartbeat ?? '—'}</span>
    </div>
  );
}

function show(job: Job) {
  setLanguage('en', false);
  return render(<Timings job={job} />);
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it('advances job and phase timers without a new progress snapshot', async () => {
  vi.useFakeTimers();
  show(
    runningJob({
      duration: 10,
      updated_at: '2026-01-01T00:00:00.000Z',
      last_progress_seconds: 1,
      stage: 'self_play',
      phase_elapsed: 4,
      phase_started_at: '2026-01-01T00:00:00.000Z',
    }),
  );
  expect(screen.getByTestId('elapsed')).toHaveTextContent('0m 10s');
  expect(screen.getByTestId('phase')).toHaveTextContent('0m 4s');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByTestId('elapsed')).toHaveTextContent('0m 13s');
  expect(screen.getByTestId('phase')).toHaveTextContent('0m 7s');
  expect(screen.getByTestId('age')).toHaveTextContent('4');
});

it('restarts the phase timer when the phase changes', async () => {
  vi.useFakeTimers();
  const base = {
    duration: 10,
    updated_at: 'a',
    last_progress_seconds: 1,
    stage: 'self_play',
    phase_elapsed: 4,
    phase_started_at: 'p1',
  };
  const { rerender } = render(<Timings job={runningJob(base)} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.getByTestId('phase')).toHaveTextContent('0m 6s');
  rerender(
    <Timings
      job={runningJob({
        ...base,
        stage: 'optimization',
        phase_elapsed: 0.5,
        phase_started_at: 'p2',
        updated_at: 'b',
      })}
    />,
  );
  expect(screen.getByTestId('phase')).toHaveTextContent('0m 0s');
});

it('never moves a timer backwards when a delayed snapshot arrives', async () => {
  vi.useFakeTimers();
  const base = { updated_at: 'a', last_progress_seconds: 0, stage: 'self_play' };
  const { rerender } = render(<Timings job={runningJob({ ...base, duration: 30 })} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(screen.getByTestId('elapsed')).toHaveTextContent('0m 35s');
  rerender(<Timings job={runningJob({ ...base, duration: 31, updated_at: 'b' })} />);
  expect(screen.getByTestId('elapsed')).toHaveTextContent('0m 35s');
});

it('freezes elapsed time for a finished job', async () => {
  vi.useFakeTimers();
  render(
    <Timings
      job={{
        id: 'job-1',
        status: 'completed',
        progress: { duration: 42, updated_at: 'a', last_progress_seconds: 0, stage: 'completed' },
      }}
    />,
  );
  expect(screen.getByTestId('elapsed')).toHaveTextContent('0m 42s');
  expect(screen.getByTestId('heartbeat')).toHaveTextContent('terminal');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(screen.getByTestId('elapsed')).toHaveTextContent('0m 42s');
});

it('reports an unresponsive worker when publication stops', async () => {
  vi.useFakeTimers();
  setLanguage('en', false);
  render(
    <TrainingLiveStatus
      job={runningJob({
        duration: 10,
        updated_at: 'fixed',
        last_progress_seconds: 1,
        stage: 'evaluation',
      })}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(25000);
  });
  expect(screen.getByRole('status')).toHaveTextContent('No recent updates');
});

it('separates a long operation from a potentially stalled one', () => {
  setLanguage('en', false);
  const { unmount } = render(
    <TrainingLiveStatus
      job={runningJob({ duration: 100, updated_at: 'a', last_progress_seconds: 200 })}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Running, long operation');
  unmount();
  render(
    <TrainingLiveStatus
      job={runningJob({ duration: 100, updated_at: 'a', last_progress_seconds: 700 })}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Potentially stalled');
});

it('renders nothing extra for jobs recorded before the monitoring fields existed', () => {
  setLanguage('en', false);
  const { container } = render(
    <TrainingLiveStatus job={runningJob({ duration: 30, iteration: 2, stage: 'self_play' })} />,
  );
  expect(container).toBeEmptyDOMElement();
});

it('renders phase counters and a bounded progress bar', () => {
  setLanguage('en', false);
  render(
    <TrainingLiveStatus
      job={runningJob({
        stage: 'self_play',
        duration: 5,
        updated_at: 'a',
        last_progress_seconds: 1,
        phase_elapsed: 4,
        phase_started_at: 'p',
        phase_progress: {
          games_completed: 18,
          games_total: 32,
          games_active: 14,
          positions: 4820,
          simulations: 9640,
        },
      })}
    />,
  );
  expect(screen.getByText('Games').nextElementSibling).toHaveTextContent('18 / 32');
  expect(screen.getByText('Active games').nextElementSibling).toHaveTextContent('14');
  expect(screen.getByText('Positions').nextElementSibling).toHaveTextContent('4,820');
  expect(screen.getByText('Phase elapsed 0m 4s')).toBeInTheDocument();
  expect(screen.getByRole('progressbar', { name: 'Phase progress' })).toHaveAttribute(
    'value',
    String(18 / 32),
  );
});

it('projects phase counters without inventing totals', () => {
  expect(
    phaseSummary(
      'self_play',
      { games_completed: 18, games_total: 32, games_active: 14, positions: 4820 },
      'en',
    ),
  ).toEqual({
    ratio: 18 / 32,
    rows: [
      { label: 'Games', value: '18 / 32' },
      { label: 'Active games', value: '14' },
      { label: 'Positions', value: '4,820' },
    ],
  });
  expect(
    phaseSummary(
      'optimization',
      {
        optimizer_steps: 120,
        optimizer_total_steps: 250,
        epoch: 1,
        policy_loss: 0.8234,
        value_loss: 0.2512,
      },
      'en',
    ),
  ).toEqual({
    ratio: 0.48,
    rows: [
      { label: 'Optimization steps', value: '120 / 250' },
      { label: 'Epoch', value: '1' },
      { label: 'Policy loss', value: '0.823' },
      { label: 'Value loss', value: '0.251' },
    ],
  });
  expect(phaseSummary('evaluation', { evaluation_games: 17, evaluation_total: 40 }, 'en')).toEqual({
    ratio: 17 / 40,
    rows: [{ label: 'Games', value: '17 / 40' }],
  });
  // Unknown totals and unsupported phases stay absent instead of becoming 0%.
  expect(phaseSummary('self_play', { games_completed: 3 }, 'en')).toEqual({
    rows: [{ label: 'Games', value: '3 / —' }],
  });
  expect(phaseSummary('checkpoint', { duration: 5 }, 'en')).toEqual({ rows: [] });
});

it('formats durations the same way the job header always has', () => {
  expect(formatDuration(0)).toBe('0m 0s');
  expect(formatDuration(3725)).toBe('1h 2m 5s');
});

it('keeps the header elapsed time in its own ticking element', async () => {
  vi.useFakeTimers();
  setLanguage('en', false);
  render(
    <LiveElapsed job={runningJob({ duration: 61, updated_at: 'a', last_progress_seconds: 0 })} />,
  );
  expect(screen.getByText(/Elapsed 1m 1s/)).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });
  expect(screen.getByText(/Elapsed 1m 5s/)).toBeInTheDocument();
  cleanup();
  const { container } = render(<LiveElapsed job={runningJob({})} />);
  expect(container).toBeEmptyDOMElement();
});
