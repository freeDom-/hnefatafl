import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ReplayControls from './ReplayControls';
import { useReplayPlayback } from './useReplayPlayback';

function Harness({ length }: { length: number }) {
  const playback = useReplayPlayback(length);
  return (
    <>
      <ReplayControls
        cursor={playback.cursor}
        length={length}
        onChange={playback.setCursor}
        playback={{ playing: playback.playing, toggle: playback.toggle }}
      />
      <button onClick={() => playback.setCursor(0)}>manual-start</button>
    </>
  );
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it('advances exactly one move per second and stops at the end', () => {
  render(<Harness length={4} />);
  expect(screen.getByText('Move 0 of 3')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  expect(screen.getByRole('button', { name: 'Stop' })).toBeVisible();
  act(() => vi.advanceTimersByTime(999));
  expect(screen.getByText('Move 0 of 3')).toBeVisible();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.getByText('Move 1 of 3')).toBeVisible();
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByText('Move 2 of 3')).toBeVisible();
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByText('Move 3 of 3')).toBeVisible();
  // Reaching the final position stops and restores the Play label.
  expect(screen.getByRole('button', { name: 'Play' })).toBeDisabled();
  act(() => vi.advanceTimersByTime(5000));
  expect(screen.getByText('Move 3 of 3')).toBeVisible();
});

it('stops immediately, retains position and does not advance further', () => {
  render(<Harness length={5} />);
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  act(() => vi.advanceTimersByTime(1000));
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByText('Move 2 of 4')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  expect(screen.getByRole('button', { name: 'Play' })).toBeVisible();
  act(() => vi.advanceTimersByTime(10000));
  expect(screen.getByText('Move 2 of 4')).toBeVisible();
});

it('disables Play for empty games and lets an earlier position play again', () => {
  const empty = render(<Harness length={0} />);
  expect(screen.getByRole('button', { name: 'Play' })).toBeDisabled();
  empty.unmount();
  render(<Harness length={3} />);
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  act(() => vi.advanceTimersByTime(1000));
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByText('Move 2 of 2')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Play' })).toBeDisabled();
  // Manual navigation stops playback and re-enables Play from an earlier move.
  fireEvent.click(screen.getByRole('button', { name: 'Previous move' }));
  expect(screen.getByText('Move 1 of 2')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Play' })).toBeEnabled();
});

it('manual navigation stops playback before changing position', () => {
  render(<Harness length={5} />);
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByRole('button', { name: 'Stop' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Next move' }));
  expect(screen.getByRole('button', { name: 'Play' })).toBeVisible();
  act(() => vi.advanceTimersByTime(10000));
  expect(screen.getByText('Move 2 of 4')).toBeVisible();
});

it('cleans up its timer on unmount', () => {
  const view = render(<Harness length={5} />);
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  view.unmount();
  expect(() => act(() => vi.advanceTimersByTime(5000))).not.toThrow();
});
