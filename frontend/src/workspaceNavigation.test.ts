import { afterEach, expect, it } from 'vitest';
import { safeDestination } from './navigation';
import {
  ARENA_DESTINATIONS,
  WORKSHOP_DESTINATIONS,
  activeDestination,
  workshopEntry,
  workspaceDestinations,
} from './workspaceNavigation';

afterEach(() => history.replaceState(null, '', '/'));

it('keeps the frozen Arena destinations and their canonical routes', () => {
  expect(ARENA_DESTINATIONS.map(({ id, label, href }) => ({ id, label, href }))).toEqual([
    { id: 'dashboard', label: 'Dashboard', href: '/dashboard' },
    { id: 'play', label: 'Play', href: '/play' },
    { id: 'statistics', label: 'Statistics', href: '/statistics' },
    { id: 'leaderboard', label: 'Leaderboard', href: '/leaderboard' },
    { id: 'archive', label: 'Hall of records', href: '/archive' },
  ]);
  expect(ARENA_DESTINATIONS.every((destination) => !destination.permission)).toBe(true);
});

it('keeps training, AI test tournaments, models and workers in the Workshop', () => {
  expect(WORKSHOP_DESTINATIONS.map(({ id, href }) => ({ id, href }))).toEqual([
    { id: 'training', href: '/training' },
    { id: 'tournaments', href: '/tournaments' },
    { id: 'model-collection', href: '/training#model-collection' },
    { id: 'workshop-workers', href: '/training#workshop-workers' },
  ]);
});

it('shows only the Workshop destinations the account is permitted to open', () => {
  expect(workspaceDestinations('workshop', []).map((item) => item.id)).toEqual([]);
  expect(workspaceDestinations('workshop', ['play']).map((item) => item.id)).toEqual([]);
  expect(workspaceDestinations('workshop', ['tournaments']).map((item) => item.id)).toEqual([
    'tournaments',
  ]);
  expect(workspaceDestinations('workshop', ['training']).map((item) => item.id)).toEqual([
    'training',
    'model-collection',
    'workshop-workers',
  ]);
  expect(
    workspaceDestinations('workshop', ['training', 'tournaments']).map((item) => item.id),
  ).toEqual(['training', 'tournaments', 'model-collection', 'workshop-workers']);
});

it('selects the accessible Workshop entry for every permission combination', () => {
  expect(workshopEntry([])).toBeNull();
  expect(workshopEntry(['play'])).toBeNull();
  expect(workshopEntry(['tournaments'])).toBe('/tournaments');
  expect(workshopEntry(['training'])).toBe('/training');
  expect(workshopEntry(['training', 'tournaments'])).toBe('/training');
  expect(workshopEntry(['models'])).toBeNull();
});

it('resolves the active destination from the route and from workshop section anchors', () => {
  expect(activeDestination('arena', 'play', '')).toBe('play');
  expect(activeDestination('arena', 'profile', '')).toBeUndefined();
  expect(activeDestination('workshop', 'training', '')).toBe('training');
  expect(activeDestination('workshop', 'training', '#model-collection')).toBe('model-collection');
  expect(activeDestination('workshop', 'training', '#workshop-workers')).toBe('workshop-workers');
  expect(activeDestination('workshop', 'training', '#unknown')).toBe('training');
});

it('keeps every destination inside the same-origin route allowlist', () => {
  for (const destination of [...ARENA_DESTINATIONS, ...WORKSHOP_DESTINATIONS]) {
    expect(safeDestination(destination.href)).toBe(destination.href);
  }
  expect(safeDestination('/games/owned-id')).toBe('/games/owned-id');
  expect(safeDestination('/online/games/online-' + 'a'.repeat(32))).toBe(
    '/online/games/online-' + 'a'.repeat(32),
  );
});
