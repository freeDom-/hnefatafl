/** Add a catalog entry and renderer/palette to extend appearance without changing game rules. */
export const COLOR_MODES = [
  { id: 'system', name: 'Follow device', description: 'Light or dark with your device' },
  { id: 'light', name: 'Light', description: 'Parchment and daylight' },
  { id: 'dark', name: 'Dark', description: 'A quiet table after dusk' },
  { id: 'wood', name: 'Wood', description: 'Timber, bronze and northern sagas' },
] as const;

export const TOKEN_THEMES = [
  { id: 'classic', name: 'Classic stones', description: 'Smooth dark, ivory and gold pieces' },
  { id: 'viking', name: 'Viking figures', description: 'Shield bearers and a crowned king' },
  { id: 'wood', name: 'Carved wood', description: 'Weathered grain and hand-carved runes' },
] as const;

export const BOARD_THEMES = [
  {
    id: 'oak',
    name: 'Golden oak',
    description: 'Warm, familiar honeyed wood',
    palette: {
      light: '#e4d3ac',
      dark: '#d3bd92',
      specialLight: '#cbb88e',
      specialDark: '#c6af82',
      frame: '#b69b6d',
      rim: '#a78a5b',
      inner: '#baa275',
      edge: '#9e875e',
      rune: '#8a6b3d',
      selected: '#9da77b',
      selectRing: '#536b3e',
      last: '#5b793b38',
      legal: '#375726c9',
    },
  },
  {
    id: 'walnut',
    name: 'Old walnut',
    description: 'Deep timber with a worn bronze edge',
    palette: {
      light: '#b69570',
      dark: '#92714f',
      specialLight: '#a2825d',
      specialDark: '#80603f',
      frame: '#624a34',
      rim: '#4d3a29',
      inner: '#816244',
      edge: '#54402e',
      rune: '#523a24',
      selected: '#9bab77',
      selectRing: '#37532b',
      last: '#d6ca7e55',
      legal: '#193824e8',
    },
  },
  {
    id: 'slate',
    name: 'Fjord slate',
    description: 'Cool stone, silver and northern blue',
    palette: {
      light: '#c4d3d5',
      dark: '#94adb3',
      specialLight: '#a9bfc4',
      specialDark: '#809fa7',
      frame: '#59747d',
      rim: '#3f5d68',
      inner: '#76949c',
      edge: '#496d77',
      rune: '#486670',
      selected: '#aec49a',
      selectRing: '#365b43',
      last: '#ecdfa855',
      legal: '#1d493acb',
    },
  },
] as const;

export type ColorMode = (typeof COLOR_MODES)[number]['id'];
export type TokenTheme = (typeof TOKEN_THEMES)[number]['id'];
export type BoardTheme = (typeof BOARD_THEMES)[number]['id'];
export interface Appearance {
  mode: ColorMode;
  tokens: TokenTheme;
  board: BoardTheme;
}
export const APPEARANCE_KEY = 'hnefatafl-appearance';
export const DEFAULT_APPEARANCE: Appearance = { mode: 'system', tokens: 'wood', board: 'walnut' };

export function parseAppearance(value: unknown): Appearance {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { ...DEFAULT_APPEARANCE };
  const candidate = value as Record<string, unknown>;
  return {
    mode: COLOR_MODES.some((mode) => mode.id === candidate.mode)
      ? (candidate.mode as ColorMode)
      : DEFAULT_APPEARANCE.mode,
    tokens: TOKEN_THEMES.some((theme) => theme.id === candidate.tokens)
      ? (candidate.tokens as TokenTheme)
      : DEFAULT_APPEARANCE.tokens,
    board: BOARD_THEMES.some((theme) => theme.id === candidate.board)
      ? (candidate.board as BoardTheme)
      : DEFAULT_APPEARANCE.board,
  };
}
