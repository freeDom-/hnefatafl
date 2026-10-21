import type { Ruleset } from './types';

/** Keep every battlefield chooser in the same small-to-large order. */
export const sortRulesets = (rulesets: Ruleset[]) =>
  [...rulesets].sort((a, b) => a.size - b.size || a.id.localeCompare(b.id));
