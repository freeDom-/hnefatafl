import type { LeagueRuleset } from './leagueTypes';

export type JoinIntent = {
  requestId: string;
  ruleset: LeagueRuleset;
  generation: string | null;
};

const uuid = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i;
const rulesets = new Set(['brandubh', 'tablut', 'copenhagen']);
const fallback = new Map<string, JoinIntent>();

export const joinIntentPrefix = (accountId: string) =>
  `hnefatafl-league-join:${encodeURIComponent(accountId)}:`;

export function rememberJoin(accountId: string, intent: JoinIntent) {
  const key = joinIntentPrefix(accountId) + intent.requestId;
  try {
    const { ruleset, generation } = intent;
    localStorage.setItem(key, JSON.stringify({ ruleset, generation }));
    fallback.delete(key);
  } catch {
    fallback.set(key, intent);
  }
}

export function forgetJoin(accountId: string, requestId: string) {
  const key = joinIntentPrefix(accountId) + requestId;
  fallback.delete(key);
  try {
    localStorage.removeItem(key);
  } catch {
    // Continue with the in-memory request state when storage is unavailable.
  }
}

export function pendingJoins(accountId: string): JoinIntent[] {
  const prefix = joinIntentPrefix(accountId);
  const entries = new Map<string, JoinIntent>();
  for (const [key, intent] of fallback) {
    if (key.startsWith(prefix)) entries.set(intent.requestId, intent);
  }
  try {
    for (let index = 0; index < localStorage.length; index++) {
      const key = localStorage.key(index);
      if (!key?.startsWith(prefix)) continue;
      const requestId = key.slice(prefix.length);
      const saved = localStorage.getItem(key);
      if (!saved || !uuid.test(requestId)) continue;
      try {
        const { generation, ruleset } = JSON.parse(saved);
        if (
          (generation === null || (typeof generation === 'string' && uuid.test(generation))) &&
          typeof ruleset === 'string' &&
          rulesets.has(ruleset)
        )
          entries.set(requestId, { requestId, generation, ruleset: ruleset as LeagueRuleset });
      } catch {
        // Ignore malformed metadata.
      }
    }
  } catch {
    // Storage access is optional for browsers that restrict it.
  }
  // Use the same recovery choice in every tab, irrespective of storage order.
  return [...entries.values()].sort((a, b) => a.requestId.localeCompare(b.requestId));
}
