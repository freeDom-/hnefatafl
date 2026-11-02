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
function Timings({ job, observedAt }: { job: Job; observedAt?: number }) {
  const live = useLiveProgress(job, observedAt);
  return (
    <div>
      <span data-testid="elapsed">
        {live.elapsed === undefined ? '—' : formatDuration(live.elapsed)}
      </span>
      <span data-testid="trial">
        {live.trialElapsed === undefined ? '—' : formatDuration(live.trialElapsed)}
      </span>
      <span data-testid="iteration">
        {live.iterationElapsed === undefined ? '—' : formatDuration(live.iterationElapsed)}
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

/** A running job inside one iteration, with every published timing. */
const iterating = {
  duration: 100,
  iteration_elapsed: 8,
  iteration_started_at: '2026-01-01T00:00:00.000Z',
  active_iteration: 42,
  phase_elapsed: 4,
  phase_started_at: '2026-01-01T00:00:04.000Z',
  updated_at: '2026-01-01T00:01:40.000Z',
  last_progress_seconds: 1,
  stage: 'self_play',
  run: 'run-a',
};

it('advances the iteration timer without a new progress snapshot', async () => {
  vi.useFakeTimers();
  render(<Timings job={runningJob(iterating)} />);
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 8s');
  expect(screen.getByTestId('phase')).toHaveTextContent('0m 4s');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 11s');
  expect(screen.getByTestId('phase')).toHaveTextContent('0m 7s');
  expect(screen.getByTestId('elapsed')).toHaveTextContent('1m 43s');
});

it('keeps the iteration timer across a phase change inside one iteration', async () => {
  vi.useFakeTimers();
  const { rerender } = render(<Timings job={runningJob(iterating)} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 10s');
  // Optimization begins inside iteration 42: only the phase timer restarts.
  rerender(
    <Timings
      job={runningJob({
        ...iterating,
        stage: 'optimization',
        phase_started_at: '2026-01-01T00:01:42.000Z',
        phase_elapsed: 0.5,
        iteration_elapsed: 10.5,
        updated_at: '2026-01-01T00:01:42.000Z',
      })}
    />,
  );
  expect(screen.getByTestId('phase')).toHaveTextContent('0m 0s');
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 10s');
});

it('restarts the iteration timer for a new active iteration', async () => {
  vi.useFakeTimers();
  const { rerender } = render(<Timings job={runningJob(iterating)} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 12s');
  // Iteration 43 enters self-play before its first game completes, and the
  // overall job duration keeps running.
  rerender(
    <Timings
      job={runningJob({
        ...iterating,
        active_iteration: 43,
        iteration_started_at: '2026-01-01T00:01:44.000Z',
        iteration_elapsed: 0.2,
        phase_started_at: '2026-01-01T00:01:44.000Z',
        phase_elapsed: 0.2,
        updated_at: '2026-01-01T00:01:44.000Z',
      })}
    />,
  );
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 0s');
  expect(screen.getByTestId('phase')).toHaveTextContent('0m 0s');
  expect(screen.getByTestId('elapsed')).toHaveTextContent('1m 44s');
});

it('restarts the iteration timer for a resumed run', () => {
  vi.useFakeTimers();
  // A resume starts a new training run: its iteration timing begins at the
  // resume, while the job duration stays the authoritative backend value.
  render(
    <Timings
      job={runningJob({
        ...iterating,
        run: 'run-b',
        iteration_elapsed: 1.5,
        duration: 4000,
        iteration_started_at: '2026-01-01T02:00:00.000Z',
        phase_started_at: '2026-01-01T02:00:00.000Z',
        phase_elapsed: 1.5,
      })}
    />,
  );
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 1s');
  expect(screen.getByTestId('elapsed')).toHaveTextContent('1h 6m 40s');
});

it('freezes the authoritative iteration duration for a finished job', async () => {
  vi.useFakeTimers();
  render(
    <Timings
      job={{
        id: 'job-1',
        status: 'completed',
        progress: { ...iterating, stage: 'completed', iteration_elapsed: 30, phase_elapsed: 2 },
      }}
    />,
  );
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 30s');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 30s');
});

it('omits iteration timing for a legacy job', () => {
  vi.useFakeTimers();
  render(
    <Timings
      job={{
        id: 'job-1',
        status: 'running',
        progress: { duration: 12, updated_at: 'a', last_progress_seconds: 0, stage: 'self_play' },
      }}
    />,
  );
  expect(screen.getByTestId('iteration')).toHaveTextContent('—');
  expect(screen.getByTestId('elapsed')).toHaveTextContent('0m 12s');
});

it('renders one iteration timer that does not re-render its siblings', async () => {
  vi.useFakeTimers();
  let siblingRenders = 0;
  function Sibling() {
    siblingRenders += 1;
    return <span>sibling</span>;
  }
  render(
    <div>
      <Timings job={runningJob(iterating)} />
      <Sibling />
    </div>,
  );
  const before = siblingRenders;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });
  // The one-second ticker stays inside the timer leaf, so a dashboard-wide
  // sibling is not re-rendered by it.
  expect(siblingRenders).toBe(before);
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 12s');
});

/** A remote job with the two server-observed ages, as the API reports them. */
const remote = (
  ages: Record<string, number>,
  progress: Record<string, unknown> = monitored,
): Job => ({ id: 'job-1', status: 'running', execution: 'remote', ...ages, progress });

it('advances the supervisor age between observations and adopts a fresh one', async () => {
  vi.useFakeTimers();
  const { rerender } = render(
    <TrainingLiveStatus
      job={remote({ publication_age_seconds: 0.4, supervisor_age_seconds: 0.4 })}
      observedAt={performance.now()}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
  // Nothing is observed for longer than the monitoring threshold, so the
  // browser reports its own blind spot rather than a worker failure.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(31000);
  });
  expect(screen.getByRole('status')).toHaveTextContent('Monitoring connection unavailable');
  // A successful observation adopts the authoritative server state again.
  rerender(
    <TrainingLiveStatus
      job={remote(
        { publication_age_seconds: 0.3, supervisor_age_seconds: 0.3 },
        { ...monitored, updated_at: '2026-01-01T00:20:00.000Z' },
      )}
      observedAt={performance.now()}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
});

it('never reports a disconnected worker from failed polling alone', async () => {
  vi.useFakeTimers();
  const { rerender } = render(
    <TrainingLiveStatus
      job={remote({ publication_age_seconds: 600, supervisor_age_seconds: 0.5 })}
      observedAt={performance.now()}
    />,
  );
  // The server confirms the child is stale while the supervisor is connected.
  expect(screen.getByRole('status')).toHaveTextContent('No recent updates');
  // The browser then loses the server for half a minute.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(31000);
  });
  const status = screen.getByRole('status');
  expect(status).toHaveTextContent('Monitoring connection unavailable');
  expect(status).not.toHaveTextContent('Worker connection lost');
  // The server confirms the supervisor went silent while the browser was away.
  rerender(
    <TrainingLiveStatus
      job={remote(
        { publication_age_seconds: 700, supervisor_age_seconds: 400 },
        { ...monitored, updated_at: '2026-01-01T00:30:00.000Z' },
      )}
      observedAt={performance.now()}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker connection lost');
});

it('adopts a resumed supervisor and a recovered child', async () => {
  vi.useFakeTimers();
  const { rerender } = render(
    <TrainingLiveStatus
      job={remote({ publication_age_seconds: 700, supervisor_age_seconds: 400 })}
      observedAt={performance.now()}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker connection lost');
  rerender(
    <TrainingLiveStatus
      job={remote(
        { publication_age_seconds: 0.2, supervisor_age_seconds: 0.2 },
        { ...monitored, updated_at: '2026-01-01T00:40:00.000Z' },
      )}
      observedAt={performance.now()}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
});

it('reports a monitoring outage for a local job without claiming a disconnection', async () => {
  vi.useFakeTimers();
  render(
    <TrainingLiveStatus
      job={{
        id: 'job-1',
        status: 'running',
        execution: 'local',
        publication_age_seconds: 0.4,
        progress: monitored,
      }}
      observedAt={performance.now()}
    />,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120000);
  });
  const status = screen.getByRole('status');
  expect(status).toHaveTextContent('Monitoring connection unavailable');
  expect(status).not.toHaveTextContent('Worker connection lost');
  expect(status).not.toHaveTextContent('No recent updates');
});

it('keeps a terminal job terminal during a monitoring outage', async () => {
  vi.useFakeTimers();
  const { rerender } = render(
    <TrainingLiveStatus
      job={remote({ publication_age_seconds: 0.4, supervisor_age_seconds: 0.4 })}
      observedAt={performance.now()}
    />,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(31000);
  });
  expect(screen.getByRole('status')).toHaveTextContent('Monitoring connection unavailable');
  rerender(
    <TrainingLiveStatus
      job={{
        id: 'job-1',
        status: 'completed',
        execution: 'remote',
        publication_age_seconds: 0.4,
        supervisor_age_seconds: 0.4,
        progress: { ...monitored, stage: 'completed' },
      }}
      observedAt={performance.now()}
    />,
  );
  // A finished job never claims a monitoring problem.
  expect(screen.queryByRole('status')).toBeNull();
});

it('resumes monitoring after a suspended tab', async () => {
  vi.useFakeTimers();
  const { rerender } = render(
    <TrainingLiveStatus
      job={remote({ publication_age_seconds: 0.4, supervisor_age_seconds: 0.4 })}
      observedAt={performance.now()}
    />,
  );
  // A suspended tab misses every poll, so the clock jumps far ahead at once.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300000);
  });
  expect(screen.getByRole('status')).toHaveTextContent('Monitoring connection unavailable');
  rerender(
    <TrainingLiveStatus
      job={remote(
        { publication_age_seconds: 0.4, supervisor_age_seconds: 0.4 },
        { ...monitored, updated_at: '2026-01-01T01:00:00.000Z' },
      )}
      observedAt={performance.now()}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Worker active');
});

it('leaves a legacy job without any heartbeat state', async () => {
  vi.useFakeTimers();
  const { container } = render(
    <TrainingLiveStatus
      job={{ id: 'job-1', status: 'running', progress: { duration: 30, stage: 'self_play' } }}
      observedAt={performance.now()}
    />,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  // A job recorded before the monitoring fields existed renders nothing extra.
  expect(container).toBeEmptyDOMElement();
});

/** A tuning job whose nested snapshot belongs to the active trial. */
const tuning = (
  trial: number,
  trialProgress: Record<string, unknown>,
  outer: Record<string, unknown> = {},
): Job => ({
  id: 'study-1',
  status: 'running',
  kind: 'tune',
  progress: {
    status: 'running',
    completed_trials: trial,
    trials_requested: 5,
    trial,
    duration: 600,
    ...outer,
    trial_progress: { run: `trial-${trial}`, ...trialProgress },
  },
});

it('keeps the study duration while a tuning trial restarts', async () => {
  vi.useFakeTimers();
  const { rerender } = render(
    <Timings
      job={tuning(0, {
        duration: 300,
        stage: 'self_play',
        phase_elapsed: 120,
        iteration_elapsed: 90,
        active_iteration: 7,
        iteration_started_at: '2026-01-01T00:00:00.000Z',
      })}
    />,
  );
  // The job elapsed is the study's, the trial timer is the trial's.
  expect(screen.getByTestId('elapsed')).toHaveTextContent('10m 0s');
  expect(screen.getByTestId('trial')).toHaveTextContent('5m 0s');
  expect(screen.getByTestId('iteration')).toHaveTextContent('1m 30s');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(screen.getByTestId('elapsed')).toHaveTextContent('11m 0s');
  expect(screen.getByTestId('trial')).toHaveTextContent('6m 0s');

  // Trial 2 starts: the study duration keeps running while the trial timer restarts.
  rerender(
    <Timings
      job={tuning(1, {
        duration: 2,
        stage: 'initializing',
        phase_elapsed: 2,
        iteration_elapsed: 1,
        active_iteration: 7,
        iteration_started_at: '2026-01-01T00:01:00.000Z',
      })}
    />,
  );
  expect(screen.getByTestId('elapsed')).toHaveTextContent('11m 0s');
  expect(screen.getByTestId('trial')).toHaveTextContent('0m 2s');
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 1s');
});

it('never reports one trial as the whole tuning job', () => {
  vi.useFakeTimers();
  // A historical tuning job without a study-level duration shows no job elapsed
  // time rather than presenting the active trial's duration as the job's.
  render(
    <Timings
      job={{
        id: 'study-1',
        status: 'running',
        kind: 'tune',
        progress: {
          status: 'running',
          completed_trials: 1,
          trials_requested: 3,
          trial: 2,
          trial_progress: { duration: 480, stage: 'optimization' },
        },
      }}
    />,
  );
  expect(screen.getByTestId('elapsed')).toHaveTextContent('—');
  // The trial's own duration is still available and named as the trial's.
  expect(screen.getByTestId('trial')).toHaveTextContent('8m 0s');
});

it('shows the study phase between trials without a trial timer', () => {
  vi.useFakeTimers();
  // A study publication keeps the last trial's data but not its live stage or
  // timers, so the dashboard shows the study's own phase and elapsed time.
  render(
    <Timings
      job={tuning(
        0,
        { history: [{ iteration: 7 }] },
        { stage: 'between_trials', phase_elapsed: 30, iteration: 7, duration: 900 },
      )}
    />,
  );
  expect(screen.getByTestId('elapsed')).toHaveTextContent('15m 0s');
  expect(screen.getByTestId('phase')).toHaveTextContent('0m 30s');
  expect(screen.getByTestId('trial')).toHaveTextContent('—');
  expect(screen.getByTestId('iteration')).toHaveTextContent('—');
});

it('keeps an ordinary training job unchanged', () => {
  vi.useFakeTimers();
  render(<Timings job={runningJob(iterating)} />);
  expect(screen.getByTestId('elapsed')).toHaveTextContent('1m 40s');
  expect(screen.getByTestId('trial')).toHaveTextContent('—');
  expect(screen.getByTestId('iteration')).toHaveTextContent('0m 8s');
});

it('shows job and phase timers but no iteration timer during initialization', () => {
  vi.useFakeTimers();
  render(
    <Timings
      job={runningJob({
        duration: 300,
        stage: 'loading_checkpoint',
        phase_elapsed: 60,
        updated_at: '2026-01-01T00:00:00.000Z',
        last_progress_seconds: 0,
      })}
    />,
  );
  expect(screen.getByTestId('elapsed')).toHaveTextContent('5m 0s');
  expect(screen.getByTestId('phase')).toHaveTextContent('1m 0s');
  // No iteration has started, so none is timed.
  expect(screen.getByTestId('iteration')).toHaveTextContent('—');
});
