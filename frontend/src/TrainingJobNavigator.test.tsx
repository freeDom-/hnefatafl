import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
afterEach(cleanup);
function rows() {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('.job-row'));
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
  expect(document.querySelector('[data-job-id="job-056"]')).toHaveAttribute('aria-current', 'true');
  expect(screen.queryByRole('button', { name: 'Show selected job' })).not.toBeInTheDocument();
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
