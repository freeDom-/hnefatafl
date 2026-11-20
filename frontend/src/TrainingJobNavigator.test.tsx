import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TrainingJobNavigator from './TrainingJobNavigator';
import { setLanguage } from './i18n';
import type { Job } from './types';
const jobs: Job[] = Array.from({ length: 57 }, (_, i) => ({
  id: `job-${String(i).padStart(3, '0')}`,
  name: `Strategist ${String(i).padStart(3, '0')}`,
  status: i % 3 === 0 ? 'running' : 'completed',
  created_at: '2026-01-01T00:00:00Z',
  config: { ruleset: 'brandubh' },
  progress: { iteration: i },
}));
beforeEach(() => setLanguage('en', false));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function stubMobileLayout(mobile: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('max-width: 900px') && mobile,
    addEventListener() {},
    removeEventListener() {},
  }));
}
function rows() {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('.job-row'));
}
// A layout stub whose responsive answer can change mid-test so mobile-to-desktop
// resizing is exercised without a second breakpoint.
function stubLayout() {
  let mobile = false;
  vi.stubGlobal('matchMedia', (query: string) => ({
    get matches() {
      return query.includes('max-width: 900px') && mobile;
    },
    addEventListener() {},
    removeEventListener() {},
  }));
  return {
    setMobile(value: boolean) {
      mobile = value;
    },
  };
}
function disclosure() {
  return screen.getByText('Find a training job').closest('details') as HTMLDetailsElement;
}
it('bounds native selector and rows to 20 while every matching job remains reachable', () => {
  render(<TrainingJobNavigator jobs={jobs} models={[]} onSelect={vi.fn()} />);
  const reached = new Set<string>();
  for (let page = 1; page <= 3; page++) {
    expect(rows().length).toBeLessThanOrEqual(20);
    expect(
      screen.getByLabelText('Selected job').querySelectorAll('option').length,
    ).toBeLessThanOrEqual(21);
    for (const row of rows()) reached.add(row.dataset.jobId!);
    expect(screen.getByText(`Page ${page} of 3`)).toBeVisible();
    if (page < 3) fireEvent.click(screen.getByRole('button', { name: 'Next job page' }));
  }
  expect(reached.size).toBe(57);
  expect(screen.getByRole('button', { name: 'Next job page' })).toBeDisabled();
});

it.each([0, 1, 20, 21, 57, 1000])(
  'keeps all %i jobs reachable with bounded rows and options',
  (count) => {
    const collection = Array.from({ length: count }, (_, i) => ({
      ...jobs[0],
      id: `bounded-${i}`,
    }));
    render(<TrainingJobNavigator jobs={collection} models={[]} onSelect={vi.fn()} />);
    const reached = new Set<string>();
    for (let page = 0; page < Math.max(1, Math.ceil(count / 20)); page++) {
      expect(rows().length).toBeLessThanOrEqual(20);
      expect(
        screen.getByLabelText('Selected job').querySelectorAll('option').length,
      ).toBeLessThanOrEqual(21);
      rows().forEach((row) => reached.add(row.dataset.jobId!));
      const next = screen.queryByRole('button', { name: 'Next job page' });
      if (next && !next.hasAttribute('disabled')) fireEvent.click(next);
    }
    expect(reached.size).toBe(count);
  },
);

it.each([0, 1, 20, 21])(
  'persists the valid page after polling shrinks to %i jobs and expands',
  (count) => {
    const onSelect = vi.fn();
    const { rerender } = render(
      <TrainingJobNavigator jobs={jobs} selected="job-000" models={[]} onSelect={onSelect} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Next job page' }));
    fireEvent.click(screen.getByRole('button', { name: 'Next job page' }));
    expect(screen.getByText('Page 3 of 3')).toBeVisible();
    rerender(
      <TrainingJobNavigator
        jobs={jobs.slice(0, count)}
        selected="job-000"
        models={[]}
        onSelect={onSelect}
      />,
    );
    expect(rows()).toHaveLength(count === 21 ? 1 : count);
    rerender(
      <TrainingJobNavigator jobs={jobs} selected="job-000" models={[]} onSelect={onSelect} />,
    );
    expect(screen.getByText(`Page ${count === 21 ? 2 : 1} of 3`)).toBeVisible();
    expect(screen.getByLabelText('Selected job')).toHaveValue('job-000');
    expect(onSelect).not.toHaveBeenCalled();
  },
);

it('preserves a browsed page on routine polls and follows only an explicit selection', () => {
  const onSelect = vi.fn();
  const { rerender } = render(
    <TrainingJobNavigator jobs={jobs} selected="job-000" models={[]} onSelect={onSelect} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Next job page' }));
  const polled = jobs.map((job) => ({ ...job, progress: { iteration: 100 } }));
  rerender(
    <TrainingJobNavigator jobs={polled} selected="job-000" models={[]} onSelect={onSelect} />,
  );
  expect(screen.getByText('Page 2 of 3')).toBeVisible();
  rerender(
    <TrainingJobNavigator jobs={polled} selected="job-056" models={[]} onSelect={onSelect} />,
  );
  expect(screen.getByText('Page 3 of 3')).toBeVisible();
  expect(document.querySelector('[data-job-id="job-056"]')).toHaveAttribute('aria-current', 'true');
  expect(onSelect).not.toHaveBeenCalled();
});
it('keeps the selected job accessible off-page and restores its page using the keyboard', async () => {
  const user = userEvent.setup();
  render(<TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Previous job page' }));
  expect(rows().some((row) => row.dataset.jobId === 'job-056')).toBe(false);
  expect(screen.getByLabelText('Selected job')).toHaveValue('job-056');
  const restore = screen.getByRole('button', { name: 'Show selected job' });
  restore.focus();
  await user.keyboard('{Enter}');
  const restored = document.querySelector('[data-job-id="job-056"]') as HTMLElement;
  expect(restored).toHaveAttribute('aria-current', 'true');
  expect(restored).toHaveFocus();
  expect(screen.queryByRole('button', { name: 'Show selected job' })).not.toBeInTheDocument();
});
it('transfers focus to the visible native selector on mobile activation', async () => {
  stubMobileLayout(true);
  const user = userEvent.setup();
  render(<TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Previous job page' }));
  const restore = screen.getByRole('button', { name: 'Show selected job' });
  restore.focus();
  await user.keyboard('{Enter}');
  expect(screen.getByLabelText('Selected job')).toHaveFocus();
});
it('restores focus for a selected job excluded by the active filters', () => {
  const onSelect = vi.fn();
  render(<TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={onSelect} />);
  fireEvent.change(screen.getByLabelText('Search jobs'), { target: { value: 'Strategist 007' } });
  fireEvent.click(screen.getByRole('button', { name: 'Show selected job' }));
  expect(screen.getByLabelText('Search jobs')).toHaveValue('');
  expect(document.querySelector('[data-job-id="job-056"]')).toHaveFocus();
  expect(screen.getByLabelText('Selected job')).toHaveValue('job-056');
  expect(onSelect).not.toHaveBeenCalled();
});
it('does not move focus during routine polling or unrelated rerenders', () => {
  const { rerender } = render(
    <TrainingJobNavigator jobs={jobs} selected="job-000" models={[]} onSelect={vi.fn()} />,
  );
  const row = document.querySelector('[data-job-id="job-000"]') as HTMLElement;
  row.focus();
  const polled = jobs.map((job) => ({ ...job, progress: { iteration: 200 } }));
  rerender(
    <TrainingJobNavigator jobs={polled} selected="job-000" models={[]} onSelect={vi.fn()} />,
  );
  expect(row).toHaveFocus();
  expect(screen.getByLabelText('Selected job')).not.toHaveFocus();
});
it('restores focus on every repeated Show selected job activation', async () => {
  const user = userEvent.setup();
  render(<TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={vi.fn()} />);
  for (let attempt = 0; attempt < 2; attempt++) {
    fireEvent.click(screen.getByRole('button', { name: 'Previous job page' }));
    const restore = screen.getByRole('button', { name: 'Show selected job' });
    restore.focus();
    await user.keyboard('{Enter}');
    expect(document.querySelector('[data-job-id="job-056"]')).toHaveFocus();
  }
});
it('renders safely when the selected job is no longer present', () => {
  render(
    <TrainingJobNavigator jobs={jobs} selected="missing-job" models={[]} onSelect={vi.fn()} />,
  );
  expect(screen.queryByRole('button', { name: 'Show selected job' })).not.toBeInTheDocument();
  expect(screen.getByLabelText('Selected job')).toHaveValue('missing-job');
});
it('restores selected jobs excluded by filters without mutating selection', () => {
  const onSelect = vi.fn();
  render(<TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={onSelect} />);
  fireEvent.change(screen.getByLabelText('Search jobs'), { target: { value: 'Strategist 007' } });
  expect(rows()).toHaveLength(1);
  expect(screen.getByLabelText('Selected job')).toHaveValue('job-056');
  fireEvent.click(screen.getByRole('button', { name: 'Show selected job' }));
  expect(screen.getByLabelText('Search jobs')).toHaveValue('');
  expect(document.querySelector('[data-job-id="job-056"]')).toBeInTheDocument();
  expect(onSelect).not.toHaveBeenCalled();
});
it('resets pagination after search or sort and clamps when polling removes pages', () => {
  const { rerender } = render(<TrainingJobNavigator jobs={jobs} models={[]} onSelect={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Next job page' }));
  fireEvent.change(screen.getByLabelText('Sort jobs'), { target: { value: 'oldest' } });
  expect(screen.getByText('Page 1 of 3')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next job page' }));
  rerender(<TrainingJobNavigator jobs={jobs.slice(0, 5)} models={[]} onSelect={vi.fn()} />);
  expect(rows()).toHaveLength(5);
  expect(screen.queryByRole('group', { name: 'Training job pages' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Search jobs'), {
    target: { value: 'no such strategist' },
  });
  expect(screen.getByText('No jobs match these filters.')).toBeVisible();
});
it('opens a collapsed desktop disclosure and focuses the restored row from the keyboard', async () => {
  const user = userEvent.setup();
  const onSelect = vi.fn();
  render(<TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={onSelect} />);
  fireEvent.click(screen.getByText('Find a training job'));
  expect(disclosure().open).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Previous job page' }));
  expect(rows().some((row) => row.dataset.jobId === 'job-056')).toBe(false);
  const restore = screen.getByRole('button', { name: 'Show selected job' });
  restore.focus();
  await user.keyboard('{Enter}');
  expect(disclosure().open).toBe(true);
  const restored = document.querySelector('[data-job-id="job-056"]') as HTMLElement;
  expect(restored).toBeVisible();
  expect(restored).toHaveFocus();
  expect(onSelect).not.toHaveBeenCalled();
});

it('opens a disclosure collapsed on mobile after the layout resizes to desktop', async () => {
  const layout = stubLayout();
  layout.setMobile(true);
  const user = userEvent.setup();
  render(<TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={vi.fn()} />);
  expect(disclosure().open).toBe(false);
  layout.setMobile(false);
  fireEvent.click(screen.getByRole('button', { name: 'Previous job page' }));
  const restore = screen.getByRole('button', { name: 'Show selected job' });
  restore.focus();
  await user.keyboard('{Enter}');
  expect(disclosure().open).toBe(true);
  expect(document.querySelector('[data-job-id="job-056"]')).toHaveFocus();
});

it('clears filters and opens a collapsed disclosure for a filtered-out selection', () => {
  render(<TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={vi.fn()} />);
  fireEvent.click(screen.getByText('Find a training job'));
  fireEvent.change(screen.getByLabelText('Search jobs'), { target: { value: 'Strategist 007' } });
  expect(rows()).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Show selected job' }));
  expect(screen.getByLabelText('Search jobs')).toHaveValue('');
  expect(disclosure().open).toBe(true);
  expect(document.querySelector('[data-job-id="job-056"]')).toHaveFocus();
});

it('discards a restoration request when the selected job disappears before it applies', () => {
  const onSelect = vi.fn();
  const { rerender } = render(
    <TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={onSelect} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Previous job page' }));
  const search = screen.getByLabelText('Search jobs');
  search.focus();
  act(() => {
    fireEvent.click(screen.getByRole('button', { name: 'Show selected job' }));
    rerender(
      <TrainingJobNavigator
        jobs={jobs.slice(0, 56)}
        selected="job-056"
        models={[]}
        onSelect={onSelect}
      />,
    );
  });
  expect(search).toHaveFocus();
  expect(rows().some((row) => row.dataset.jobId === 'job-056')).toBe(false);
  expect(document.querySelector('.job-row:focus')).toBeNull();
  expect(onSelect).not.toHaveBeenCalled();
});

it('discards a restoration request when the selection changes before it applies', () => {
  const onSelect = vi.fn();
  const { rerender } = render(
    <TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={onSelect} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Previous job page' }));
  const search = screen.getByLabelText('Search jobs');
  search.focus();
  act(() => {
    fireEvent.click(screen.getByRole('button', { name: 'Show selected job' }));
    rerender(
      <TrainingJobNavigator jobs={jobs} selected="job-000" models={[]} onSelect={onSelect} />,
    );
  });
  expect(search).toHaveFocus();
  expect(document.querySelector('[data-job-id="job-000"]')).not.toHaveFocus();
  expect(document.querySelector('.job-row:focus')).toBeNull();
});

it('does not replay a completed restoration request during later polling', () => {
  const onSelect = vi.fn();
  const { rerender } = render(
    <TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={onSelect} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Previous job page' }));
  fireEvent.click(screen.getByRole('button', { name: 'Show selected job' }));
  // The explicit request lands on the intended row.
  expect(document.querySelector('[data-job-id="job-056"]')).toHaveFocus();
  // Move focus elsewhere before the next poll so a replay would be visible.
  const other = document.querySelector('[data-job-id="job-040"]') as HTMLElement;
  other.focus();
  const polled = jobs.map((job) => ({ ...job, progress: { iteration: 500 } }));
  rerender(
    <TrainingJobNavigator jobs={polled} selected="job-056" models={[]} onSelect={onSelect} />,
  );
  expect(other).toHaveFocus();
  expect(document.querySelector('[data-job-id="job-056"]')).not.toHaveFocus();
  expect(onSelect).not.toHaveBeenCalled();
});

it('does not replay a cancelled request when the removed job reappears', () => {
  const onSelect = vi.fn();
  const { rerender } = render(
    <TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={onSelect} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Previous job page' }));
  const search = screen.getByLabelText('Search jobs');
  search.focus();
  act(() => {
    fireEvent.click(screen.getByRole('button', { name: 'Show selected job' }));
    rerender(
      <TrainingJobNavigator
        jobs={jobs.slice(0, 56)}
        selected="job-056"
        models={[]}
        onSelect={onSelect}
      />,
    );
  });
  // The job vanished before the request applied, so it was discarded.
  expect(search).toHaveFocus();
  // Polling restores the job; the cancelled request must not replay.
  rerender(<TrainingJobNavigator jobs={jobs} selected="job-056" models={[]} onSelect={onSelect} />);
  expect(search).toHaveFocus();
  expect(document.querySelector('[data-job-id="job-056"]')).not.toHaveFocus();
  expect(onSelect).not.toHaveBeenCalled();
});

it('native selector and keyboard activation select the intended job without losing ids', async () => {
  const onSelect = vi.fn();
  const user = userEvent.setup();
  render(<TrainingJobNavigator jobs={jobs} models={[]} onSelect={onSelect} />);
  const row = rows()[4];
  row.focus();
  await user.keyboard('{Enter}');
  expect(onSelect).toHaveBeenLastCalledWith(jobs.find((job) => job.id === row.dataset.jobId));
  const select = screen.getByLabelText('Selected job');
  const option = select.querySelectorAll('option')[3] as HTMLOptionElement;
  fireEvent.change(select, { target: { value: option.value } });
  expect(onSelect).toHaveBeenLastCalledWith(jobs.find((job) => job.id === option.value));
});
