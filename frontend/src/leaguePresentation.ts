import { ApiError } from './api';
import type { Translate } from './i18n';
import type { LeagueRuleset, LeagueSeason, LeagueTier } from './leagueTypes';

export const RULESET_LABELS: Record<LeagueRuleset, string> = {
  brandubh: 'Brandubh',
  tablut: 'Tablut',
  copenhagen: 'Copenhagen',
};

export const TIER_LABELS: Record<LeagueTier, string> = {
  midgard: 'Midgard',
  nidavellir: 'Nidavellir',
  alfheim: 'Alfheim',
  asgard: 'Asgard',
  valhalla: 'Valhalla',
};

export const SEASON_LABELS: Record<LeagueSeason['status'], string> = {
  current: 'Provisional standings',
  provisional: 'Provisional standings',
  finalized: 'Finalized standings',
  rebuilding: 'Standings being rebuilt',
};

const errorLabels: Record<string, string> = {
  league_invalid_request: 'The league request is invalid.',
  league_deployment_locked: 'The league is temporarily read-only while deployment is in progress.',
  league_conflict: 'Your league state changed. Check the current status and try again.',
  league_rate_limited: 'League request limit reached. Try again later.',
  league_ineligible: 'Matchmaking requires an active, verified account with permission to play.',
  league_request_missing: 'This league queue request was not found.',
  league_unavailable: 'The league service is temporarily unavailable.',
  league_page_changed: 'These league records changed. Return to the first page to continue.',
  league_season_missing: 'This league season was not found.',
  league_invalid_cursor: 'This league page is no longer available. Return to the first page.',
  league_version_changed: 'This league record changed. Review the updated state before retrying.',
  league_rebuilding: 'A rebuild is already running for this season.',
  league_queue_not_stale: 'This queue entry is no longer stale.',
};

export function leagueError(error: unknown, t: Translate) {
  if (error instanceof ApiError) {
    if (error.code && Object.hasOwn(errorLabels, error.code)) return t(errorLabels[error.code]);
    if (error.status === 401) return t('Sign in to continue.');
    if (error.status === 403) return t('This league action is unavailable to your account.');
    if (error.status === 503) return t('The league service is temporarily unavailable.');
  }
  return t('The league request could not be completed. Try again.');
}

export function leagueMonth(value: string, locale: string) {
  return new Intl.DateTimeFormat(locale, {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(value));
}

export function leagueDate(value: string, locale: string) {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(
    new Date(value),
  );
}
