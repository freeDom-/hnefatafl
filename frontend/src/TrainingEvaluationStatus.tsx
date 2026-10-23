import { useI18n } from './i18n';
import { opponentName } from './EvaluationSettings';

export interface EvaluationRecord {
  opponent?: string;
  opponent_identity?: string;
  opponent_config?: { depth?: number; time_limit?: number };
  context?: Record<string, unknown>;
  iteration?: number;
  score?: number;
  candidate_as_attacker?: { score: number };
  candidate_as_defender?: { score: number };
  wins?: number;
  losses?: number;
  draws?: number;
  truncated?: number;
  duration?: number;
  champion_iteration?: number;
  promoted?: boolean;
  benchmark_saturated?: boolean;
  score_lower_bound?: number;
  required_score?: number;
}
export interface Selection {
  opponent: string;
  champion_iteration?: number;
  champion_provisional?: boolean;
  context?: {
    games: number;
    max_plies: number;
    candidate_config: { iterations: number; time_limit: number };
    opponent_config: { depth?: number; time_limit: number };
  };
  benchmarks?: {
    iteration: number;
    score: number;
    opponent_identity: string;
    context: { opponent: string };
  }[];
  last_evaluation?: EvaluationRecord;
}

export default function TrainingEvaluationStatus({ progress }: { progress: unknown }) {
  const { t, locale } = useI18n();
  const state = progress as { iteration?: number; evaluation_selection?: Selection } | undefined;
  const selection = state?.evaluation_selection;
  if (!selection) return null;
  const last = selection.last_evaluation;
  const format = (value: number) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: 3 }).format(value);
  return (
    <section className="training-evaluation-status" aria-label={t('Evaluation strategy')}>
      <h4>
        {t('Evaluation strategy')}: {t(opponentName(selection.opponent))}
      </h4>
      <dl className="job-metrics">
        <div>
          <dt>{t('Latest training iteration')}</dt>
          <dd>{state.iteration ?? '—'}</dd>
        </div>
        <div>
          <dt>{t('Accepted champion iteration')}</dt>
          <dd>
            {selection.champion_iteration ?? '—'}
            {selection.champion_provisional ? ` · ${t('Provisional incumbent')}` : ''}
          </dd>
        </div>
        {last && (
          <>
            <div>
              <dt>{t('Last evaluation iteration')}</dt>
              <dd>{last.iteration}</dd>
            </div>
            <div>
              <dt>
                {last.opponent === 'champion'
                  ? t('Candidate versus champion score')
                  : `${t('Evaluation score')} · ${t(opponentName(last.opponent ?? 'shield_bearer'))}`}
              </dt>
              <dd>{last.score === undefined ? '—' : format(last.score)}</dd>
            </div>
            <div>
              <dt>{t('Attacker-side score')}</dt>
              <dd>{last.candidate_as_attacker ? format(last.candidate_as_attacker.score) : '—'}</dd>
            </div>
            <div>
              <dt>{t('Defender-side score')}</dt>
              <dd>{last.candidate_as_defender ? format(last.candidate_as_defender.score) : '—'}</dd>
            </div>
            <div>
              <dt>{t('Wins')}</dt>
              <dd>{last.wins ?? '—'}</dd>
            </div>
            <div>
              <dt>{t('Losses')}</dt>
              <dd>{last.losses ?? '—'}</dd>
            </div>
            <div>
              <dt>{t('Rules draws')}</dt>
              <dd>{last.draws ?? '—'}</dd>
            </div>
            <div>
              <dt>{t('Unfinished games')}</dt>
              <dd>{last.truncated ?? '—'}</dd>
            </div>
            <div>
              <dt>{t('Evaluation duration (seconds)')}</dt>
              <dd>{last.duration === undefined ? '—' : format(last.duration)}</dd>
            </div>
            {last.opponent === 'champion' && last.score_lower_bound !== undefined && (
              <div>
                <dt>{t('Champion score lower bound')}</dt>
                <dd>
                  {format(last.score_lower_bound)}
                  {last.required_score !== undefined
                    ? ` · ${t('Required score')}: >${format(last.required_score)}`
                    : ''}
                </dd>
              </div>
            )}
          </>
        )}
      </dl>
      {selection.context && (
        <p className="subtle">
          {t(
            'Effective evaluation: {games} games, {plies} ply limit, {visits} neural simulations, {seconds}s per neural move.',
            {
              games: selection.context.games,
              plies: selection.context.max_plies,
              visits: selection.context.candidate_config.iterations,
              seconds: selection.context.candidate_config.time_limit,
            },
          )}{' '}
          {t('Opponent deadline: {seconds}s.', {
            seconds: selection.context.opponent_config.time_limit,
          })}
          {selection.context.opponent_config.depth !== undefined &&
            ` ${t('Jarl depth: {depth}.', { depth: selection.context.opponent_config.depth })}`}
        </p>
      )}
      {last?.benchmark_saturated && (
        <p role="status">
          {t(
            'This benchmark is near saturation and provides little discrimination. Consider a stronger evaluation opponent.',
          )}
        </p>
      )}
      {last?.promoted !== undefined && (
        <p>
          {t(
            last.promoted
              ? 'Candidate promoted to champion.'
              : 'Champion retained; improvement was not established.',
          )}
        </p>
      )}
      {!!selection.benchmarks?.length && (
        <details>
          <summary>{t('Best fixed-benchmark checkpoints')}</summary>
          <ul>
            {selection.benchmarks.map((best) => (
              <li key={best.opponent_identity} title={best.opponent_identity}>
                {t(opponentName(best.context.opponent))} · {t('Iteration')} {best.iteration} ·{' '}
                {format(best.score)} · <code>{best.opponent_identity}</code>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
