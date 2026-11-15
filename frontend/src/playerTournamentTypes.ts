/** Published human-tournament DTOs; no private account or moderation fields. */
export type PlayerTournamentStatus = 'registration' | 'running' | 'complete' | 'cancelled';
export type TournamentPlayer = {
  account_id: string;
  name: string;
  username?: string;
  portrait_id?: string | null;
};
export type PlayerTournament = {
  id: string;
  name: string;
  ruleset: string;
  format: 'double_elimination';
  status: PlayerTournamentStatus;
  review_state: 'ok' | 'disputed';
  capacity: number;
  match_legs: number;
  time_per_move: number;
  bracket_size: number | null;
  entrant_count: number;
  version: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  registration_deadline: string | null;
  /** Server clock/capacity projection, not account eligibility. */
  registration_open?: boolean;
  scheduled_start_at?: string | null;
  official_winner: TournamentPlayer | null;
};
export type TournamentEntrant = TournamentPlayer & {
  seed: number | null;
  registered_at: string;
  /** Authoritative match losses, not individual leg results. */
  losses?: number;
  eliminated?: boolean;
};
export type PlayerTournamentDetail = PlayerTournament & {
  entrants: TournamentEntrant[];
  match_counts: Partial<Record<TournamentMatch['status'], number>>;
};
export type TournamentMatch = {
  id: string;
  bracket: 'upper' | 'lower' | 'grand_final' | 'reset';
  round: number;
  slot: number;
  status: 'pending' | 'ready' | 'complete' | 'bye' | 'disputed';
  /** Source edges supplied by the authority: seed:N, winner:ID or loser:ID. */
  source_a: string;
  source_b: string;
  decided_by: 'play' | 'higher_seed' | 'undefeated_advantage' | 'bye' | 'admin_override' | null;
  participants: { a: TournamentPlayer | null; b: TournamentPlayer | null };
  winner: TournamentPlayer | null;
  loser: TournamentPlayer | null;
  legs: {
    leg: number;
    game_id: string;
    game_status: string | null;
    outcome: 'a' | 'b' | 'draw' | 'void' | null;
    result_revision: number | null;
    settled_at: string | null;
  }[];
  created_at: string;
  settled_at: string | null;
};
export type TournamentRound = { round: number; matches: TournamentMatch[] };
export type PlayerTournamentBracket = {
  tournament_id: string;
  version?: number;
  status: PlayerTournamentStatus;
  review_state: 'ok' | 'disputed';
  bracket_size: number | null;
  upper: TournamentRound[];
  lower: TournamentRound[];
  final: TournamentMatch | null;
  reset: TournamentMatch | null;
};
export type PlayerTournamentPage = {
  items: PlayerTournament[];
  next_cursor: string | null;
  limit: number;
};
