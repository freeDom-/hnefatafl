import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import DashboardCountdown from './DashboardCountdown';

const start = Date.parse('2035-01-01T00:00:00Z');
const deadline = (seconds: number) => new Date(start + seconds * 1000).toISOString();
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(start);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
it('ticks a 45-second server deadline and clamps expiration without keeping an interval', () => {
  render(<DashboardCountdown deadline={deadline(45)} />);
  expect(screen.getByText('45s')).toBeVisible();
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByText('44s')).toBeVisible();
  act(() => vi.advanceTimersByTime(44000));
  expect(screen.getByText('0s')).toBeVisible();
  expect(vi.getTimerCount()).toBe(0);
  act(() => vi.advanceTimersByTime(5000));
  expect(screen.getByText('0s')).toBeVisible();
});
it('crosses the minute boundary and follows changed server deadlines', () => {
  const view = render(<DashboardCountdown deadline={deadline(61)} />);
  expect(screen.getByText('1m 01s')).toBeVisible();
  act(() => vi.advanceTimersByTime(2000));
  expect(screen.getByText('59s')).toBeVisible();
  view.rerender(<DashboardCountdown deadline={deadline(122)} />);
  expect(screen.getByText('2m 00s')).toBeVisible();
  expect(vi.getTimerCount()).toBe(1);
});
it('cleans up on game/account changes and unmount', () => {
  const view = render(<DashboardCountdown key="account-a:game-a" deadline={deadline(45)} />);
  view.rerender(<DashboardCountdown key="account-b:game-b" deadline={deadline(90)} />);
  expect(screen.getByText('1m 30s')).toBeVisible();
  expect(vi.getTimerCount()).toBe(1);
  view.unmount();
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  expect(vi.getTimerCount()).toBe(0);
});
it('stops ticking when hidden and immediately resynchronizes on visibility', () => {
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  render(<DashboardCountdown deadline={deadline(45)} />);
  hidden.mockReturnValue(true);
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  expect(vi.getTimerCount()).toBe(0);
  act(() => vi.advanceTimersByTime(10000));
  hidden.mockReturnValue(false);
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  expect(screen.getByText('35s')).toBeVisible();
  expect(vi.getTimerCount()).toBe(1);
});
it('does not schedule an expired or invalid deadline or an aria-live announcement', () => {
  const view = render(<DashboardCountdown deadline="invalid" />);
  expect(vi.getTimerCount()).toBe(0);
  view.rerender(<DashboardCountdown deadline={deadline(-1)} />);
  expect(screen.getByText('0s')).toBeVisible();
  expect(document.querySelector('[aria-live]')).toBeNull();
  expect(vi.getTimerCount()).toBe(0);
});

it('does not rerender its parent or sibling content on clock ticks', () => {
  const Parent = vi.fn(() => (
    <>
      <p>Stable dashboard content</p>
      <DashboardCountdown deadline={deadline(45)} />
    </>
  ));
  render(<Parent />);
  const before = Parent.mock.calls.length;
  act(() => vi.advanceTimersByTime(3000));
  expect(screen.getByText('42s')).toBeVisible();
  expect(Parent).toHaveBeenCalledTimes(before);
});
it('restarts only when the server supplies a future deadline after expiration', () => {
  const view = render(<DashboardCountdown deadline={deadline(1)} />);
  act(() => vi.advanceTimersByTime(2000));
  expect(vi.getTimerCount()).toBe(0);
  view.rerender(<DashboardCountdown deadline={deadline(12)} />);
  expect(screen.getByText('10s')).toBeVisible();
  expect(vi.getTimerCount()).toBe(1);
});
