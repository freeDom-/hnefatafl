/** Shared Arena/Workshop destinations. Presentation only: every href keeps its
 * existing canonical route, including the `/training` section anchors for the
 * model collection and remote worker controls that already live on that page. */
import { useEffect, useState } from 'react';

export type Workspace = 'arena' | 'workshop';

export type WorkspaceDestination = {
  id: string;
  /** Full label for the persistent header navigation. */
  label: string;
  /** Short label for the compact mobile navigation. */
  short: string;
  href: string;
  /** Permission required before the destination may be displayed at all. */
  permission?: string;
};

export const ARENA_DESTINATIONS: WorkspaceDestination[] = [
  { id: 'dashboard', label: 'Dashboard', short: 'Home', href: '/dashboard' },
  { id: 'play', label: 'Play', short: 'Play', href: '/play' },
  { id: 'statistics', label: 'Statistics', short: 'Stats', href: '/statistics' },
  { id: 'leaderboard', label: 'Leaderboard', short: 'Compete', href: '/leaderboard' },
  { id: 'archive', label: 'Hall of records', short: 'Replays', href: '/archive' },
];

/** Training, AI test tournaments, models and workers stay in the Workshop; the
 * existing `/tournaments` namespace remains AI evaluation only. */
export const WORKSHOP_DESTINATIONS: WorkspaceDestination[] = [
  {
    id: 'training',
    label: 'Training',
    short: 'Training',
    href: '/training',
    permission: 'training',
  },
  {
    id: 'tournaments',
    label: 'AI testing',
    short: 'Testing',
    href: '/tournaments',
    permission: 'tournaments',
  },
  {
    id: 'model-collection',
    label: 'Models',
    short: 'Models',
    href: '/training#model-collection',
    permission: 'training',
  },
  {
    id: 'workshop-workers',
    label: 'Workers',
    short: 'Workers',
    href: '/training#workshop-workers',
    permission: 'training',
  },
];

/** Destinations the account may actually open; unauthorized ones are never shown. */
export function workspaceDestinations(
  workspace: Workspace,
  permissions: string[],
): WorkspaceDestination[] {
  const destinations = workspace === 'workshop' ? WORKSHOP_DESTINATIONS : ARENA_DESTINATIONS;
  return destinations.filter(
    (destination) => !destination.permission || permissions.includes(destination.permission),
  );
}

/** First Workshop surface the account may open, or null when none is authorized. */
export function workshopEntry(permissions: string[]): string | null {
  if (permissions.includes('training')) return '/training';
  if (permissions.includes('tournaments')) return '/tournaments';
  return null;
}

/** Route hash, so `#` section anchors can refresh the active destination. */
export function useLocationHash() {
  const [hash, setHash] = useState(() => location.hash);
  useEffect(() => {
    const changed = () => setHash(location.hash);
    window.addEventListener('popstate', changed);
    window.addEventListener('hashchange', changed);
    return () => {
      window.removeEventListener('popstate', changed);
      window.removeEventListener('hashchange', changed);
    };
  }, []);
  return hash;
}

/** The navigation id that owns the current route, including `#` section anchors. */
export function activeDestination(
  workspace: Workspace,
  current: string | undefined,
  hash: string,
): string | undefined {
  const destinations = workspace === 'workshop' ? WORKSHOP_DESTINATIONS : ARENA_DESTINATIONS;
  const section = hash.replace(/^#/, '');
  if (section && destinations.some((destination) => destination.id === section)) return section;
  return destinations.some((destination) => destination.id === current) ? current : undefined;
}
