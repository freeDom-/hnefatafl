import { LEAGUES_DESTINATION } from './workspaceNavigation';

/** Desktop-rail information architecture from the approved Arena references. */
export type SidebarItem =
  | { kind: 'destination'; id: string; label: string; href: string }
  | { kind: 'group'; id: string; label: string; children: SidebarItem[] };

export const ARENA_SIDEBAR_ITEMS: SidebarItem[] = [
  { kind: 'destination', id: 'dashboard', label: 'The Great Hall', href: '/dashboard' },
  { kind: 'destination', id: 'play', label: 'Battlefield', href: '/play' },
  { kind: 'destination', id: 'online', label: 'Online Play', href: '/online' },
  { kind: 'destination', id: 'statistics', label: 'Statistics', href: '/statistics' },
  {
    kind: 'group',
    id: 'compete',
    label: 'Trials of Valor',
    children: [
      { kind: 'destination', id: 'leaderboard', label: 'Leaderboard', href: '/leaderboard' },
      {
        kind: 'destination',
        id: 'player-tournaments',
        label: 'Player tournaments',
        href: '/player-tournaments',
      },
      { kind: 'destination', ...LEAGUES_DESTINATION },
    ],
  },
  { kind: 'destination', id: 'archive', label: 'Hall of records', href: '/archive' },
  { kind: 'destination', id: 'portraits', label: 'Hall of Renown', href: '/portraits' },
];

/** The stable destination id the workshop entry links to, for `aria-current`. */
export function workshopDestinationId(href: string | null): string | undefined {
  if (href === '/training') return 'training';
  if (href === '/tournaments') return 'tournaments';
  return undefined;
}
