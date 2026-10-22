import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import TournamentWinMatrix, { tournamentWinMatrix } from './TournamentWinMatrix';
import type { TournamentGame, TournamentPlayer } from './tournamentTypes';
import { setLanguage } from './i18n';

const players: TournamentPlayer[] = [
  { name: 'Old alias', model_name: 'Freydis', rune: 'ᚠ', config: { algorithm: 'neural' } },
  { name: 'Ulf', rune: 'ᚢ', config: { algorithm: 'baseline' } },
  { name: 'Other alias', model_name: 'Freydis', rune: 'ᚦ', config: { algorithm: 'neural' } },
  {
    name: 'Old profile alias',
    profile_name: 'Jarl',
    rune: 'ᚨ',
    agent_rune: 'ᚱ',
    config: { algorithm: 'alpha_beta' },
  },
];

function match(changes: Partial<TournamentGame> = {}): TournamentGame {
  return {
    ruleset: 'brandubh',
    winner: 'attackers',
    outcome: 'completed',
    reason: 'king_captured',
    plies: 5,
    moves: [],
    attacker_player: 0,
    defender_player: 1,
    average_decision_time: 0.01,
    total_decision_time: 0.05,
    ...changes,
  };
}

const games: TournamentGame[] = [
  match(),
  match({ attacker_player: 1, defender_player: 0, winner: 'defenders' }),
  match({ attacker_player: 1, defender_player: 0 }),
  match({ attacker_player: 2, defender_player: 0, winner: 'defenders' }),
  match({ attacker_player: 3, defender_player: 2, winner: 'defenders' }),
  match({ attacker_player: 2, defender_player: 3 }),
  match({ attacker_player: 3, defender_player: 1 }),
  match({ attacker_player: 0, defender_player: 2, winner: 'draw' }),
  match({ attacker_player: 1, defender_player: 3, winner: null, outcome: 'truncated' }),
  // A partial record must never contribute a win, even if it has a stale winner.
  match({ attacker_player: 1, defender_player: 3, outcome: 'truncated' }),
  match({ ruleset: 'tablut', attacker_player: 3, defender_player: 0 }),
];

const expected = [
  [0, 2, 1, 0],
  [1, 0, 0, 0],
  [0, 0, 0, 2],
  [0, 1, 0, 0],
];

function cell(table: HTMLElement, row: number, column: number) {
  const found = table.querySelector<HTMLElement>(
    `td[data-row-player="${row}"][data-column-player="${column}"]`,
  );
  expect(found).not.toBeNull();
  return found!;
}

beforeEach(() => setLanguage('en', false));
afterEach(() => {
  cleanup();
  setLanguage('en', false);
});

describe('tournament battlefield win matrix', () => {
  it('counts row wins against each column across both sides without merging fields or outcomes', () => {
    const original = JSON.stringify(games);
    expect(tournamentWinMatrix(4, games, 'brandubh')).toEqual(expected);
    expect(tournamentWinMatrix(4, games, 'tablut')).toEqual([
      [0, 0, 0, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
      [1, 0, 0, 0],
    ]);
    expect(JSON.stringify(games)).toBe(original);
  });

  it('reads legacy two-player side assignments without inventing missing opponents in larger series', () => {
    const legacy = [
      match({ defender_player: undefined }),
      match({ defender_player: undefined, winner: 'defenders' }),
      match({ attacker_player: 1, defender_player: undefined, winner: 'defenders' }),
    ];
    expect(tournamentWinMatrix(2, legacy, 'brandubh')).toEqual([
      [0, 2],
      [1, 0],
    ]);
    expect(tournamentWinMatrix(3, legacy, 'brandubh')).toEqual([
      [0, 0, 0],
      [0, 0, 0],
      [0, 0, 0],
    ]);
  });

  it('renders distinct numbered seats, canonical names and the actual opponent runes on the diagonal', () => {
    render(
      <TournamentWinMatrix
        players={players}
        games={games}
        ruleset="brandubh"
        battlefield="Brandubh"
      />,
    );
    const table = screen.getByRole('table', { name: 'Head-to-head wins · Brandubh' });
    expect(table).toHaveAttribute('data-ruleset', 'brandubh');
    const columns = within(table).getAllByRole('columnheader');
    const rows = within(table).getAllByRole('rowheader');
    expect(columns).toHaveLength(5);
    expect(rows).toHaveLength(4);
    for (const [index, name] of ['Freydis', 'Ulf', 'Freydis', 'Jarl'].entries()) {
      for (const header of [columns[index + 1], rows[index]]) {
        expect(header).toHaveTextContent(`Opponent ${index + 1}`);
        expect(header).toHaveTextContent(name);
      }
    }
    expect(table).not.toHaveTextContent('Old alias');
    expect(table).not.toHaveTextContent('Other alias');
    expect(table).not.toHaveTextContent('Old profile alias');
    for (const [index, rune] of ['ᚠ', 'ᚢ', 'ᚦ', 'ᚱ'].entries()) {
      expect(cell(table, index, index).querySelector('[data-rune]')).toHaveAttribute(
        'data-rune',
        rune,
      );
    }
    for (let row = 0; row < players.length; row++) {
      for (let column = 0; column < players.length; column++) {
        if (row !== column)
          expect(cell(table, row, column)).toHaveTextContent(String(expected[row][column]));
      }
    }
    expect(table.closest('.tournament-win-matrix-scroll')).toHaveAttribute('tabindex', '0');
    expect(document.getElementById(table.getAttribute('aria-describedby')!)).toHaveTextContent(
      "Cells show the row opponent's wins against the column opponent. Draws and unfinished games do not count as wins.",
    );
  });

  it('updates each direction independently when further completed matches arrive', () => {
    const show = (matches: TournamentGame[]) => (
      <TournamentWinMatrix
        players={players.slice(0, 2)}
        games={matches}
        ruleset="brandubh"
        battlefield="Brandubh"
      />
    );
    const view = render(show([match()]));
    const table = screen.getByRole('table');
    expect(cell(table, 0, 1)).toHaveTextContent('1');
    expect(cell(table, 1, 0)).toHaveTextContent('0');
    view.rerender(show([match(), match({ attacker_player: 1, defender_player: 0 })]));
    expect(cell(table, 0, 1)).toHaveTextContent('1');
    expect(cell(table, 1, 0)).toHaveTextContent('1');
    expect(cell(table, 0, 0).querySelector('[data-rune]')).toHaveAttribute('data-rune', 'ᚠ');
  });

  it('keeps every opponent visible before any wins and does not fabricate a missing legacy rune', () => {
    const noRune = { ...players[1], rune: undefined };
    render(
      <TournamentWinMatrix
        players={[players[0], noRune, players[2]]}
        games={[]}
        ruleset="brandubh"
        battlefield="Brandubh"
      />,
    );
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('rowheader')).toHaveLength(3);
    for (let row = 0; row < 3; row++) {
      for (let column = 0; column < 3; column++) {
        if (row !== column) expect(cell(table, row, column)).toHaveTextContent('0');
      }
    }
    expect(cell(table, 1, 1).querySelector('[data-rune]')).toBeNull();
    expect(cell(table, 1, 1)).toHaveTextContent('—');
  });

  it('localizes the table, numbered headers and explanation while keeping opponent identities', () => {
    setLanguage('de', false);
    render(
      <TournamentWinMatrix
        players={players.slice(0, 2)}
        games={games}
        ruleset="brandubh"
        battlefield="Brandubh"
      />,
    );
    const table = screen.getByRole('table', { name: 'Siege im Direktvergleich · Brandubh' });
    expect(within(table).getAllByRole('rowheader')[0]).toHaveTextContent('Gegner 1');
    expect(table).toHaveTextContent('Freydis');
    expect(table).not.toHaveTextContent('Opponent');
    expect(document.getElementById(table.getAttribute('aria-describedby')!)).toHaveTextContent(
      'Jede Zelle zeigt die Siege des Zeilengegners gegen den Spaltengegner. Remis und unbeendete Partien zählen nicht als Siege.',
    );
  });
});
