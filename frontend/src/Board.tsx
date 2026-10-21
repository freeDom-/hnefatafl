import { useRef, type KeyboardEvent } from 'react';
import type { Game } from './types';
import { Token } from './Tokens';
import { useAppearance } from './Appearance';

export const coordinate = (index: number, size: number) =>
  `${String.fromCharCode(97 + (index % size))}${size - Math.floor(index / size)}`;
const pieceNames: Record<string, string> = {
  '.': 'empty',
  A: 'attacker',
  D: 'defender',
  K: 'king',
};

export { Crown } from './Tokens';

interface Props {
  game: Game;
  selected: number | null;
  onSelect: (index: number) => void;
  disabled?: boolean;
}
export default function Board({ game, selected, onSelect, disabled = false }: Props) {
  const { appearance } = useAppearance();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const targets = new Set(
    game.legal_moves.filter((move) => move.source === selected).map((move) => move.target),
  );
  const lastMove = game.cursor > 0 ? game.moves[game.cursor - 1] : undefined;
  const keydown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === 'ArrowRight') next = Math.min(index + 1, game.board.length - 1);
    else if (event.key === 'ArrowLeft') next = Math.max(index - 1, 0);
    else if (event.key === 'ArrowUp') next = Math.max(index - game.size, 0);
    else if (event.key === 'ArrowDown') next = Math.min(index + game.size, game.board.length - 1);
    else if (event.key === 'Escape') {
      onSelect(-1);
      return;
    } else return;
    event.preventDefault();
    refs.current[next]?.focus();
  };
  return (
    <div
      className="board-with-labels"
      data-board-theme={appearance.board}
      data-token-theme={appearance.tokens}
    >
      <div className="board-ranks" aria-hidden="true">
        {Array.from({ length: game.size }, (_, i) => (
          <span key={i}>{game.size - i}</span>
        ))}
      </div>
      <div className="board-frame">
        <div
          className="board"
          role="grid"
          aria-label={`${game.ruleset} game board`}
          style={{ gridTemplateColumns: `repeat(${game.size}, 1fr)` }}
        >
          {game.board.map((piece, index) => {
            const row = Math.floor(index / game.size),
              column = index % game.size;
            const throne =
              row === Math.floor(game.size / 2) && column === Math.floor(game.size / 2);
            const corner =
              game.ruleset !== 'tablut' &&
              (row === 0 || row === game.size - 1) &&
              (column === 0 || column === game.size - 1);
            const last = lastMove && (lastMove.source === index || lastMove.target === index);
            return (
              <button
                key={index}
                ref={(el) => {
                  refs.current[index] = el;
                }}
                role="gridcell"
                type="button"
                className={`square ${(row + column) % 2 ? 'dark' : 'light'} ${selected === index ? 'selected' : ''} ${targets.has(index) ? 'legal' : ''} ${last ? 'last-move' : ''} ${throne || corner ? 'special' : ''}`}
                onClick={() => onSelect(index)}
                disabled={disabled}
                onKeyDown={(event) => keydown(event, index)}
                aria-label={`${coordinate(index, game.size)}, ${pieceNames[piece] ?? piece}${targets.has(index) ? ', legal move' : ''}`}
                aria-selected={selected === index}
              >
                {piece !== '.' ? (
                  <Token piece={piece} theme={appearance.tokens} />
                ) : corner || throne ? (
                  <span className="square-rune" aria-hidden="true">
                    {corner ? 'ᛟ' : 'ᛉ'}
                  </span>
                ) : null}
                {targets.has(index) && <span className="move-dot" aria-hidden="true" />}
              </button>
            );
          })}
        </div>
      </div>
      <div className="board-files" aria-hidden="true">
        {Array.from({ length: game.size }, (_, i) => (
          <span key={i}>{String.fromCharCode(97 + i)}</span>
        ))}
      </div>
    </div>
  );
}
