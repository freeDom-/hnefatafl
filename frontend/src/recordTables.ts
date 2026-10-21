import { parameterSummary } from './RecordsParameters';
import type { Game, Ruleset, StatRow } from './types';

export type StatSortKey =
  | 'name'
  | 'games'
  | 'attacker_win_rate'
  | 'defender_win_rate'
  | 'draw_rate'
  | 'outcomes'
  | 'rates'
  | 'average_game_length';

export interface StatSort {
  key: StatSortKey;
  direction: 'ascending' | 'descending';
}

const finite = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value) ? value : 0;
const normalize = (value: string) => value.trim().toLocaleLowerCase();
const compareText = (first: string, second: string) =>
  first.localeCompare(second, undefined, { sensitivity: 'base', numeric: true });

function tuple(row: StatRow, key: StatSortKey): number[] {
  if (key === 'outcomes') return [row.wins, row.draws, row.losses].map(finite);
  if (key === 'rates') return [row.win_rate, row.draw_rate, row.loss_rate].map(finite);
  if (key === 'average_game_length') return [finite(row.average_game_length ?? row.average_length)];
  return [finite(row[key as keyof StatRow])];
}

/** Keep ties deterministic without reversing their identity order with numeric values. */
export function sortStatRows(
  rows: StatRow[],
  sort: StatSort | null,
  group: 'ruleset' | 'player',
  rulesets: Ruleset[],
): StatRow[] {
  if (!sort) return [...rows];
  const names = new Map(rulesets.map((rule) => [rule.id, rule.name]));
  const name = (row: StatRow) =>
    group === 'ruleset'
      ? (names.get(row.ruleset ?? '') ?? row.name ?? row.ruleset ?? '')
      : (row.name ?? '');
  return rows
    .map((row, index) => ({ row, index }))
    .sort((first, second) => {
      let order = 0;
      if (sort.key === 'name') order = compareText(name(first.row), name(second.row));
      else {
        const a = tuple(first.row, sort.key);
        const b = tuple(second.row, sort.key);
        for (let index = 0; index < a.length; index++) {
          order = a[index] - b[index];
          if (order) break;
        }
      }
      if (order) return sort.direction === 'descending' ? -order : order;
      return (
        compareText(
          first.row.id ?? first.row.ruleset ?? '',
          second.row.id ?? second.row.ruleset ?? '',
        ) || first.index - second.index
      );
    })
    .map(({ row }) => row);
}

const profileNames: Record<string, string> = {
  baseline: 'Shield bearer',
  alpha_beta: 'Jarl',
  mcts: 'Seer',
  neural: 'Trained model',
};

/** A single compact secondary line; the dialog retains the complete parameters. */
export function statPlayerDescription(row: StatRow): string {
  if (row.kind !== 'ai') return row.kind === 'human' ? 'Human' : '';
  const profile = row.profile_name ?? profileNames[row.profile_id ?? String(row.config?.algorithm)];
  const parts = ['Computer', profile, row.model_name, ...parameterSummary(row.config).split(' · ')];
  const seen = new Set<string>();
  return parts
    .filter((part): part is string => {
      if (!part?.trim()) return false;
      const key = normalize(part);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .join(' · ');
}

export function matchesPlayer(row: StatRow, query: string): boolean {
  const needle = normalize(query);
  return !needle || normalize(`${row.name ?? ''} ${statPlayerDescription(row)}`).includes(needle);
}

export function matchesRecordedGame(game: Game, query: string): boolean {
  const needle = normalize(query);
  if (!needle) return true;
  return Object.values(game.players).some((player) =>
    normalize(
      [player.name, player.profile_name, player.model_name].filter(Boolean).join(' '),
    ).includes(needle),
  );
}
