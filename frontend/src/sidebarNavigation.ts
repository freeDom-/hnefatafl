/** Desktop-rail information architecture from the approved Arena references.
 * Real destinations reuse the canonical `workspaceNavigation` ids and hrefs, so
 * `aria-current` and every deep link keep working. Entries without a built route
 * are explicit placeholders rather than links to a fabricated page. */
export type SidebarItem =
  | { kind: 'destination'; id: string; label: string; href: string }
  | { kind: 'placeholder'; id: string; label: string }
  | { kind: 'group'; id: string; label: string; children: SidebarItem[] };

export const ARENA_SIDEBAR_ITEMS: SidebarItem[] = [
  { kind: 'destination', id: 'dashboard', label: 'Home', href: '/dashboard' },
  { kind: 'destination', id: 'play', label: 'Play & board', href: '/play' },
  { kind: 'destination', id: 'statistics', label: 'Statistics', href: '/statistics' },
  {
    kind: 'group',
    id: 'compete',
    label: 'Compete',
    children: [
      { kind: 'destination', id: 'leaderboard', label: 'Leaderboard', href: '/leaderboard' },
      { kind: 'placeholder', id: 'tournaments', label: 'Tournaments' },
      { kind: 'placeholder', id: 'leagues', label: 'Leagues' },
    ],
  },
  { kind: 'destination', id: 'archive', label: 'Replays & analysis', href: '/archive' },
  { kind: 'placeholder', id: 'portraits', label: 'Portrait hall' },
];

/** The stable destination id the workshop entry links to, for `aria-current`. */
export function workshopDestinationId(href: string | null): string | undefined {
  if (href === '/training') return 'training';
  if (href === '/tournaments') return 'tournaments';
  return undefined;
}
