import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { PlayTabsProvider } from './PlayTabs';
import DesktopInspector from './DesktopInspector';
import MobileGameLayout from './MobileGameLayout';

afterEach(cleanup);
it.each(['desktop', 'mobile'])(
  'retains the selected %s tab across games and handles unavailable tabs',
  (mode) => {
    const layout = (id: string, analytics = true) => (
      <PlayTabsProvider>
        {mode === 'desktop' ? (
          <DesktopInspector
            key={id}
            history={<p>History {id}</p>}
            analytics={analytics ? <p>Analysis {id}</p> : undefined}
            moveCount={2}
            switcher={<p>Running games</p>}
          />
        ) : (
          <MobileGameLayout
            key={id}
            presentation={{
              title: id,
              subtitle: 'Game',
              players: <p>Players</p>,
              moveCount: 2,
              analytics: analytics ? <p>Analysis {id}</p> : undefined,
            }}
            battlefield={<div role="grid" />}
            history={<p>History {id}</p>}
            switcher={<p>Running games</p>}
          />
        )}
      </PlayTabsProvider>
    );
    const view = render(layout('one'));
    fireEvent.click(screen.getByRole('tab', { name: 'Analytics' }));
    view.rerender(layout('two'));
    expect(screen.getByRole('tab', { name: 'Analytics' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Analysis two')).toBeVisible();
    view.rerender(layout('online', false));
    expect(screen.getByRole('tab', { name: 'Moves 2' })).toHaveAttribute('aria-selected', 'true');
    view.rerender(layout('three'));
    expect(screen.getByRole('tab', { name: 'Analytics' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('tab', { name: 'My games' }));
    view.rerender(layout('four'));
    expect(screen.getByRole('tab', { name: 'My games' })).toHaveAttribute('aria-selected', 'true');
  },
);
