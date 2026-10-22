import { useI18n } from './i18n';
import type { Model } from './types';
import { modelIteration } from './modelLabel';
import './modelSummary.css';

/** These are saved training budgets, rather than the search settings used for a tournament. */
export default function ModelTrainingSummary({
  model,
  compact = false,
}: {
  model: Model;
  compact?: boolean;
}) {
  const { t, locale } = useI18n();
  const config = model.training_config ?? {};
  const number = (value: unknown) =>
    typeof value === 'number' && Number.isFinite(value) ? value : undefined;
  const channels = number(model.architecture?.channels) ?? number(config.channels);
  const blocks = number(model.architecture?.blocks) ?? number(config.blocks);
  const total = number(config.iterations);
  const iteration = modelIteration(model);
  const format = (value: number) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: 8, useGrouping: false }).format(value);
  const games = number(config.games);
  const visits = number(config.mcts_iterations);
  const seconds = number(config.mcts_time_limit);
  const rate = number(config.learning_rate);
  const epochs = number(config.epochs);
  const batch = number(config.batch_size);
  const seed = number(config.seed);
  const optimizerNames: Record<string, string> = { adamw: 'AdamW', adam: 'Adam', sgd: 'SGD' };
  const optimizer =
    typeof config.optimizer === 'string'
      ? (optimizerNames[config.optimizer] ?? config.optimizer)
      : undefined;
  return (
    <dl
      className={`model-training-summary ${compact ? 'compact' : ''}`}
      aria-label={t('Model network and training')}
    >
      <div>
        <dt>{t('Network')}</dt>
        <dd>
          {channels !== undefined && blocks !== undefined
            ? t('Policy / value · {channels} channels · {blocks} blocks', { channels, blocks })
            : t('Not recorded')}
        </dd>
      </div>
      <div>
        <dt>{t('Training iteration')}</dt>
        <dd>
          {iteration !== undefined
            ? total !== undefined && total >= iteration
              ? t('{iteration} / {total} planned', { iteration, total })
              : String(iteration)
            : t('Not recorded')}
        </dd>
      </div>
      <div>
        <dt>{t('Self-play')}</dt>
        <dd>
          {games !== undefined
            ? t(games === 1 ? '{games} game / iteration' : '{games} games / iteration', { games })
            : t('Not recorded')}
        </dd>
      </div>
      <div>
        <dt>{t('Training search')}</dt>
        <dd>
          {[
            visits !== undefined ? t('{visits} MCTS iterations', { visits }) : undefined,
            seconds !== undefined
              ? t('{seconds}s / move', { seconds: format(seconds) })
              : undefined,
          ]
            .filter(Boolean)
            .join(' · ') || t('Not recorded')}
        </dd>
      </div>
      <div>
        <dt>{t('Learning')}</dt>
        <dd>
          {[optimizer, rate !== undefined ? t('LR {rate}', { rate: format(rate) }) : undefined]
            .filter(Boolean)
            .join(' · ') || t('Not recorded')}
        </dd>
      </div>
      {!compact && (
        <div>
          <dt>{t('Training batches')}</dt>
          <dd>
            {[
              epochs !== undefined
                ? t(epochs === 1 ? '{epochs} epoch' : '{epochs} epochs', { epochs })
                : undefined,
              batch !== undefined ? t('batch {batch}', { batch }) : undefined,
            ]
              .filter(Boolean)
              .join(' · ') || t('Not recorded')}
          </dd>
        </div>
      )}
      <div className="model-training-seed">
        <dt>{t('Training seed')}</dt>{' '}
        <dd>
          <strong>
            {seed !== undefined && Number.isInteger(seed) && seed >= 0 && seed <= 4294967295
              ? seed
              : t('Not recorded')}
          </strong>
        </dd>
      </div>
    </dl>
  );
}
