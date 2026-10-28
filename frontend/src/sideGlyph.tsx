import type { Side } from './types';

export const sideGlyph = (side: Side) => (
  <span className={`mini-piece ${side === 'attackers' ? 'black' : 'ivory'}`} aria-hidden="true" />
);
