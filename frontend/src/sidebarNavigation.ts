/** Desktop-rail information architecture from the approved Arena references.
 * Real destinations reuse the canonical `workspaceNavigation` ids and hrefs, so
 * `aria-current` and every deep link keep working. Entries without a built route
 * are explicit placeholders rather than links to a fabricated page. */
export type SidebarItem =
  | { kind: 'destination'; id: string; label: string; href: string }
  | { kind: 'placeholder'; id: string; label: string }
  | { kind: 'group'; id: string; label: string; children: SidebarItem[] };

export const ARENA_SIDEBAR_ITEMS: SidebarItem[] = [
  { kind: 'destination', id: 'dashboard', label: 'The Great Hall', href: '/dashboard' },
  { kind: 'destination', id: 'play', label: 'Battlefield', href: '/play' },
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
      { kind: 'placeholder', id: 'leagues', label: 'Leagues' },
    ],
  },
  { kind: 'destination', id: 'archive', label: 'Replays & analysis', href: '/archive' },
  { kind: 'destination', id: 'portraits', label: 'Hall of Renown', href: '/portraits' },
];

/** The stable destination id the workshop entry links to, for `aria-current`. */
export function workshopDestinationId(href: string | null): string | undefined {
  if (href === '/training') return 'training';
  if (href === '/tournaments') return 'tournaments';
  return undefined;
}
