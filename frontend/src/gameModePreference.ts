import type { Mode } from './types';

export const GAME_MODE_KEY = 'hnefatafl-last-game-mode';

/** Preferences must never prevent a game from starting when storage is unavailable. */
export function readLastGameMode(): Mode {
  try {
    const mode = localStorage.getItem(GAME_MODE_KEY);
    return mode === 'human-human' || mode === 'human-ai' || mode === 'ai-ai' ? mode : 'human-human';
  } catch {
    return 'human-human';
  }
}

export function rememberGameMode(mode: Mode): void {
  try {
    localStorage.setItem(GAME_MODE_KEY, mode);
  } catch {
    // The server-created game remains usable without browser preference storage.
  }
}
