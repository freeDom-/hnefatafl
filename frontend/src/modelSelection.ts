import type { Model } from './types';

export interface ModelRecommendation {
  model: Model;
  score: number | null;
  games: number;
  basis: 'evaluation' | 'latest';
}

function baselineEvaluation(model: Model): { score: number; games: number } | null {
  const evaluation = model.evaluation;
  if (!evaluation || (evaluation.opponent !== undefined && evaluation.opponent !== 'baseline'))
    return null;
  const counts = [evaluation.wins, evaluation.draws, evaluation.losses, evaluation.truncated].map(
    (value) => value ?? 0,
  );
  if (counts.some((value) => !Number.isSafeInteger(value) || value < 0)) return null;
  const [wins, draws, losses, unfinished] = counts;
  const games = wins + draws + losses + unfinished;
  if (!Number.isSafeInteger(games) || !games) return null;
  const score = (wins + draws / 2) / games;
  if (
    evaluation.score !== undefined &&
    (!Number.isFinite(evaluation.score) ||
      evaluation.score < 0 ||
      evaluation.score > 1 ||
      Math.abs(evaluation.score - score) > 0.000001)
  )
    return null;
  return { score, games };
}

const timestamp = (model: Model) => {
  const value = model.created_at ? Date.parse(model.created_at) : NaN;
  return Number.isFinite(value) ? value : 0;
};

/** Rank comparable baseline evaluations, never training loss or model version. */
export function selectStrongestModel(models: Model[], ruleset: string): ModelRecommendation | null {
  const candidates = models
    .filter((model) => model.ruleset === ruleset)
    .map((model): ModelRecommendation => {
      const evaluation = baselineEvaluation(model);
      return {
        model,
        score: evaluation?.score ?? null,
        games: evaluation?.games ?? 0,
        basis: evaluation ? 'evaluation' : 'latest',
      };
    });
  candidates.sort(
    (a, b) =>
      Number(b.basis === 'evaluation') - Number(a.basis === 'evaluation') ||
      (b.score ?? 0) - (a.score ?? 0) ||
      b.games - a.games ||
      timestamp(b.model) - timestamp(a.model) ||
      a.model.id.localeCompare(b.model.id),
  );
  return candidates[0] ?? null;
}
