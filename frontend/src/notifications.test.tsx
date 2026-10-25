import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NotificationViewport, clearNotifications, notify } from './notifications';
import { api, errorMessage } from './api';

afterEach(() => {
  cleanup();
  clearNotifications();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('shared notifications', () => {
  it('announces errors, keeps them until dismissed and never moves focus', () => {
    render(
      <>
        <NotificationViewport />
        <button type="button">Keep focus</button>
      </>,
    );
    const keep = screen.getByRole('button', { name: 'Keep focus' });
    keep.focus();
    act(() => {
      notify.error('The table is full.');
    });
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('The table is full.');
    expect(document.activeElement).toBe(keep);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('keeps error notifications after the success timeout window', () => {
    vi.useFakeTimers();
    render(<NotificationViewport />);
    act(() => {
      notify.error('Sticky failure');
    });
    act(() => {
      vi.advanceTimersByTime(60000);
    });
    expect(screen.getByRole('alert')).toHaveTextContent('Sticky failure');
  });

  it('auto-dismisses success and info notifications', () => {
    vi.useFakeTimers();
    render(<NotificationViewport />);
    act(() => {
      notify.success('Saved');
      notify.info('Heads up');
    });
    const statuses = screen.getAllByRole('status');
    expect(statuses).toHaveLength(2);
    expect(statuses.map((status) => status.textContent)).toEqual(
      expect.arrayContaining([
        expect.stringContaining('Saved'),
        expect.stringContaining('Heads up'),
      ]),
    );
    act(() => {
      vi.advanceTimersByTime(6000);
    });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('deduplicates identical messages instead of flooding the screen', () => {
    render(<NotificationViewport />);
    act(() => {
      notify.error('Connection unavailable');
      notify.error('Connection unavailable');
      notify.error('Connection unavailable');
    });
    expect(screen.getAllByRole('alert')).toHaveLength(1);
  });

  it('bounds visible notifications and reveals queued ones after dismissal', () => {
    render(<NotificationViewport />);
    act(() => {
      for (const message of ['first', 'second', 'third', 'fourth']) notify.error(message);
    });
    expect(screen.getAllByRole('alert')).toHaveLength(3);
    expect(screen.queryByText('fourth')).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Dismiss notification' })[0]);
    expect(screen.getByText('fourth')).toBeInTheDocument();
    expect(screen.queryByText('first')).not.toBeInTheDocument();
  });

  it('uses a clear fallback when no useful error message is available', () => {
    render(<NotificationViewport />);
    act(() => {
      notify.error('');
    });
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong. Please try again.');
    expect(errorMessage({})).toBe('Something went wrong. Please try again.');
    expect(errorMessage(new Error('   '))).toBe('Something went wrong. Please try again.');
  });

  it('falls back safely when an API error payload is missing or unexpected', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: false,
          status: 500,
          statusText: '',
          json: async () => {
            throw new Error('not json');
          },
        } as unknown as Response),
      ),
    );
    await expect(api('/broken')).rejects.toThrow('Request failed (500).');
  });
});
