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

/** A healthy monitored snapshot published by a running worker. */
const monitored = {
  duration: 100,
  updated_at: '2026-01-01T00:00:00.000Z',
  last_progress_seconds: 1,
  stage: 'self_play',
  phase_elapsed: 5,
  phase_started_at: 'p',
};

it('classifies an already-stale snapshot as unresponsive immediately after mounting', () => {
  setLanguage('en', false);
  render(
    <TrainingLiveStatus
      job={{ id: 'job-1', status: 'running', publication_age_seconds: 600, progress: monitored }}
    />,
  );
  // No client-side grace period: the server-observed age decides right away.
  expect(screen.getByRole('status')).toHaveTextContent('No recent updates');
});

it('keeps a freshly published snapshot active on mount', () => {
  setLanguage('en', false);
  render(
    <TrainingLiveStatus
      job={{ id: 'job-1', status: 'running', publication_age_seconds: 0.5, progress: monitored }}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
});

it('recovers from a stale snapshot when the worker republishes', () => {
  setLanguage('en', false);
  const { rerender } = render(
    <TrainingLiveStatus
      job={{ id: 'job-1', status: 'running', publication_age_seconds: 600, progress: monitored }}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('No recent updates');
  rerender(
    <TrainingLiveStatus
      job={{
        id: 'job-1',
        status: 'running',
        publication_age_seconds: 0.2,
        progress: { ...monitored, updated_at: '2026-01-01T00:10:00.000Z' },
      }}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
});

it('separates publication freshness from stalled computation on a skewed worker clock', () => {
  setLanguage('en', false);
  render(
    <TrainingLiveStatus
      job={{
        id: 'job-1',
        status: 'running',
        publication_age_seconds: 0.5,
        // The remote worker's wall clock is wildly wrong; only the server age counts.
        progress: {
          ...monitored,
          updated_at: '1999-01-01T00:00:00.000Z',
          last_progress_seconds: 700,
        },
      }}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Potentially stalled');
});

it('ages the server publication timestamp with the local ticker', async () => {
  vi.useFakeTimers();
  setLanguage('en', false);
  render(
    <TrainingLiveStatus
      job={{ id: 'job-1', status: 'running', publication_age_seconds: 5, progress: monitored }}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20000);
  });
  expect(screen.getByRole('status')).toHaveTextContent('No recent updates');
});

it('adopts the authoritative duration when a running job completes', async () => {
  vi.useFakeTimers();
  const progress = { ...monitored, duration: 40, phase_elapsed: 4 };
  const { rerender } = render(<Timings job={runningJob(progress)} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20000);
  });
  // The browser had extrapolated a minute of elapsed time.
  expect(screen.getByTestId('elapsed')).toHaveTextContent('1m 0s');
  rerender(
    <Timings
      job={{
        id: 'job-1',
        status: 'completed',
        progress: {
          ...progress,
          duration: 58,
          phase_elapsed: 6,
          updated_at: 'b',
          stage: 'completed',
        },
      }}
    />,
  );
  expect(screen.getByTestId('elapsed')).toHaveTextContent('0m 58s');
  expect(screen.getByTestId('phase')).toHaveTextContent('0m 6s');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(screen.getByTestId('elapsed')).toHaveTextContent('0m 58s');
});

it.each(['failed', 'cancelled', 'pruned'])(
  'freezes the authoritative duration for a %s job',
  (status) => {
    render(
      <Timings
        job={{
          id: 'job-1',
          status,
          progress: { duration: 12, updated_at: 'a', last_progress_seconds: 0, stage: status },
        }}
      />,
    );
    expect(screen.getByTestId('elapsed')).toHaveTextContent('0m 12s');
    expect(screen.getByTestId('heartbeat')).toHaveTextContent('terminal');
  },
);

it('shows the stored duration of a historical completed job', () => {
  render(
    <Timings
      job={{ id: 'job-1', status: 'completed', progress: { duration: 3725, iteration: 3 } }}
    />,
  );
  expect(screen.getByTestId('elapsed')).toHaveTextContent('1h 2m 5s');
  // Jobs recorded before the monitoring fields existed invent no heartbeat.
  expect(screen.getByTestId('heartbeat')).toHaveTextContent('—');
});

it('distinguishes a stale child from a silent remote supervisor', () => {
  setLanguage('en', false);
  const remote = (ages: Record<string, number>, updatedAt = monitored.updated_at) => ({
    id: 'job-1',
    status: 'running',
    execution: 'remote' as const,
    ...ages,
    progress: { ...monitored, updated_at: updatedAt },
  });
  // The supervisor reports every couple of seconds; the child stopped.
  const { rerender } = render(
    <TrainingLiveStatus
      job={remote({ publication_age_seconds: 600, supervisor_age_seconds: 1 })}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('No recent updates');

  // The supervisor itself is silent, so the child's state is unknown.
  rerender(
    <TrainingLiveStatus
      job={remote({ publication_age_seconds: 600, supervisor_age_seconds: 600 })}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker connection lost');

  // The child publishes again, so both signals are fresh.
  rerender(
    <TrainingLiveStatus
      job={remote(
        { publication_age_seconds: 0.4, supervisor_age_seconds: 0.4 },
        '2026-01-01T00:05:00.000Z',
      )}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
});

it('never claims a connection loss for a local job', () => {
  setLanguage('en', false);
  render(
    <TrainingLiveStatus
      job={{
        id: 'job-1',
        status: 'running',
        execution: 'local',
        publication_age_seconds: 0.5,
        progress: monitored,
      }}
    />,
  );
  // A local job has no supervisor signal at all.
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
});

it('ages the child and supervisor signals together on a remote job', async () => {
  vi.useFakeTimers();
  setLanguage('en', false);
  render(
    <TrainingLiveStatus
      job={{
        id: 'job-1',
        status: 'running',
        execution: 'remote',
        publication_age_seconds: 1,
        supervisor_age_seconds: 1,
        progress: monitored,
      }}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
  // Only the child age is anchored locally; the supervisor age comes from the
  // server, which stops advancing it once the connection is gone.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(25000);
  });
  expect(screen.getByRole('status')).toHaveTextContent('No recent updates');
});
