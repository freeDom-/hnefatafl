export type LeagueRuleset = 'brandubh' | 'tablut' | 'copenhagen';
export type LeagueTier = 'midgard' | 'nidavellir' | 'alfheim' | 'asgard' | 'valhalla';
export type LeagueTierThreshold = { id: LeagueTier; minimum_points: number };

export type LeagueSeason = {
  id: string;
  starts_at: string;
  ends_at: string;
  grace_ends_at: string;
  status: 'current' | 'provisional' | 'finalized' | 'rebuilding';
  revision: number;
  stored: boolean;
};

type LeagueRecord = { points: number; wins: number; draws: number; losses: number; played: number };

export type LeagueProgress = LeagueRecord & {
  tier: LeagueTier | null;
  next_tier:
    | (LeagueTierThreshold & {
        points_needed: number;
        progress_points: number;
        progress_total: number;
      })
    | null;
  previous_tier?: LeagueTier | null;
  tier_changed_at?: string | null;
  qualified: boolean;
  qualifying_results: number;
  rank: number | null;
};

export type LeaguePolicy = {
  version: number;
  rulesets: LeagueRuleset[];
  tiers: LeagueTierThreshold[];
  win_points: number;
  draw_points: number;
  loss_points: number;
  qualifying_results: number;
  grace_seconds: number;
  queue_seconds: number;
  clock_seconds: number;
  increment_seconds: number;
  rating_window: { initial: number; maximum: number; step: number; interval_seconds: number };
  pair_daily_limit: number;
};

export type LeagueQueueEntry = {
  status: 'queued' | 'expired' | 'cancelled';
  request_id: string;
  ruleset: LeagueRuleset;
  joined_at: string;
  expires_at: string;
  rating_window: number;
};

export type LeagueQueueStatus = {
  status: 'idle' | 'queued' | 'expired' | 'cancelled' | 'matched';
  active_game: { game_id: string; ruleset: LeagueRuleset } | null;
  queue: LeagueQueueEntry | null;
  join_generation: string | null;
  eligible: boolean;
  paused: boolean;
  enabled: boolean;
  clocks_enabled: boolean;
  server_time: string;
};

export type CurrentLeague = {
  server_time: string;
  season: LeagueSeason;
  policy: LeaguePolicy;
  progress: LeagueProgress | null;
  queue: LeagueQueueStatus;
};

export type LeaguePlayer = {
  id: string;
  username: string;
  display_name: string;
  portrait_id: string | null;
};

export type LeagueStanding = LeagueRecord & {
  rank: number;
  player: LeaguePlayer;
  tier: LeagueTier;
};

export type LeagueGame = {
  game_id: string;
  game_status: 'waiting' | 'active' | 'completed' | 'voided' | 'cancelled';
  paired_at: string;
  opponent: LeaguePlayer;
  side: 'attackers' | 'defenders';
  result: 'win' | 'draw' | 'loss' | null;
  state: 'pending' | 'eligible' | 'late' | 'withdrawn';
  corrected: boolean;
  result_revision: number | null;
  game_href: string;
  replay_href: string | null;
};

export type LeaguePage<T> = { items: T[]; next_cursor: string | null };
export type LeagueSeasonPage<T> = LeaguePage<T> & { season: LeagueSeason };

export type LeagueSeasonProgress = { season: LeagueSeason; progress: LeagueProgress | null };

export type StaleLeagueEntry = Pick<
  LeagueQueueEntry,
  'request_id' | 'ruleset' | 'joined_at' | 'expires_at'
> & { reason: 'expired' | 'disabled' | 'unverified' | 'ineligible' };

export type LeagueHealth = {
  settings: { paused: boolean; version: number };
  worker: {
    running: boolean;
    last_checked_at: string | null;
    last_success_at: string | null;
    last_error: string | null;
    state:
      | 'starting'
      | 'idle'
      | 'processed'
      | 'deployment_locked'
      | 'settlement_retry'
      | 'rebuild_retry'
      | 'processing_deferred';
    consecutive_failures: number;
  };
  queue: {
    total: number;
    expired: number;
    by_ruleset: Partial<Record<LeagueRuleset, number>>;
    stale: StaleLeagueEntry[];
    stale_next_cursor: string | null;
  };
  settlement: {
    pending: number;
    failed: number;
    oldest_pending_seconds: number | null;
    max_attempts: number;
    next_retry_at: string | null;
  };
  rebuilds: { season_id: string; stage: string; attempts: number; due_at: string }[];
  rebuild_pending: number;
  rebuild_retries: number;
};

export type LeagueAudit = {
  sequence: number;
  action: string;
  season_id: string | null;
  at: string;
  reason: string;
  actor_id: string | null;
};
