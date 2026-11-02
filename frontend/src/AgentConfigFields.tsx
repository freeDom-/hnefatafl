/**
 * The shared custom-opponent editor.
 *
 * The tournament participant form and the single-player "Custom opponent"
 * section render exactly this component, so a setting added here becomes
 * available to both without a second implementation. The parent supplies the
 * card container, the profile/opponent selector and the persisted identity.
 */
import HeuristicSelect, { heuristicFromConfig } from './HeuristicSelect';
import ModelTrainingSummary from './ModelTrainingSummary';
import SearchWrapper from './SearchWrapper';
import {
  DEFAULT_ALPHA_BETA_SEARCH,
  TABLE_CAPACITY_MAX,
  TABLE_CAPACITY_MIN,
  effectiveSearch,
  presetSearch,
  searchFromConfig,
  searchPreset,
  type AlphaBetaPresetName,
  type AlphaBetaSearchSettings,
} from './alphaBetaSearch';
import { useI18n } from './i18n';
import { modelOptionLabel } from './modelLabel';
import { selectStrongestModel } from './modelSelection';
import type { TournamentPlayer } from './tournamentTypes';
import type { AIProfile, Config, Model } from './types';
import './tournaments.css';

export function AlphaBetaSearchFields({
  prefix,
  search,
  onPreset,
  onField,
  modelBacked = false,
}: {
  prefix: string;
  search: AlphaBetaSearchSettings;
  onPreset: (name: AlphaBetaPresetName) => void;
  onField: (patch: Partial<AlphaBetaSearchSettings>) => void;
  /** Neural evaluation cannot reuse symmetric positions, so those switches are off. */
  modelBacked?: boolean;
}) {
  const { t, locale } = useI18n();
  const preset = searchPreset(search);
  // Legacy overrides every optimized flag, so the irrelevant switches and the
  // table capacity are disabled instead of showing active-looking settings that
  // the backend ignores.
  const legacy = search.legacy;
  const toggle = (
    key:
      | 'legacy'
      | 'transposition'
      | 'move_ordering'
      | 'symmetry_transposition'
      | 'symmetry_pruning',
    label: string,
    tooltip: string,
    disabled = false,
  ) => (
    <label key={key} title={t(tooltip)}>
      <input
        type="checkbox"
        aria-label={`${prefix} ${t(label).toLocaleLowerCase(locale)}`}
        checked={search[key]}
        disabled={disabled}
        onChange={(event) => onField({ [key]: event.target.checked })}
      />
      {t(label)}
    </label>
  );
  return (
    <fieldset className="tournament-search-optimizations">
      <legend>{t('Alpha-beta optimizations')}</legend>
      <label>
        {t('Search preset')}
        <select
          aria-label={`${prefix} ${t('search preset').toLocaleLowerCase(locale)}`}
          value={preset}
          onChange={(event) => onPreset(event.target.value as AlphaBetaPresetName)}
        >
          <option value="legacy">{t('Legacy alpha-beta')}</option>
          <option value="optimized">{t('Optimized default')}</option>
          {!modelBacked && <option value="symmetry">{t('Full symmetry')}</option>}
          <option value="custom">{t('Custom')}</option>
        </select>
      </label>
      {legacy && (
        <p className="subtle">
          {t(
            'Legacy uses the original exact-value cache search. Every optimization switch and the table capacity below are ignored while it is selected.',
          )}
        </p>
      )}
      <div className="form-grid tournament-search-flags">
        {toggle(
          'legacy',
          'Legacy search',
          'Use the original exact-value cache search and ignore every optimized option below.',
        )}
        {toggle(
          'transposition',
          'Transposition table',
          'Reuse positions that were already searched during this game.',
          legacy,
        )}
        {toggle(
          'move_ordering',
          'Improved move ordering',
          'Try known-good moves earlier using the transposition table, principal variation, killers and history.',
          legacy,
        )}
        {toggle(
          'symmetry_transposition',
          'D4-canonical transpositions',
          'Share positions that are rotations or reflections of each other.',
          legacy || modelBacked,
        )}
        {toggle(
          'symmetry_pruning',
          'Symmetry move pruning',
          'Search one representative per symmetric move group.',
          legacy || modelBacked,
        )}
      </div>
      {modelBacked && (
        <p className="subtle">
          {t(
            'Neural evaluation cannot reuse rotated or reflected positions, so symmetry search is unavailable.',
          )}
        </p>
      )}
      <label
        title={t('Bounded number of stored transposition entries. Larger tables use more memory.')}
      >
        {t('Transposition table capacity')}
        <input
          type="number"
          aria-label={`${prefix} ${t('transposition table capacity').toLocaleLowerCase(locale)}`}
          min={TABLE_CAPACITY_MIN}
          max={TABLE_CAPACITY_MAX}
          step={1}
          required
          disabled={legacy}
          value={search.table_capacity}
          onChange={(event) => onField({ table_capacity: Number(event.target.value) })}
        />
      </label>
    </fieldset>
  );
}

/** The default participant for a round-robin seat, alternating baseline/Jarl. */
export const fallbackPlayer = (index: number): TournamentPlayer =>
  index % 2
    ? {
        name: 'Jarl',
        profile_id: 'alpha_beta',
        config: { algorithm: 'alpha_beta', depth: 5, time_limit: 2 },
      }
    : {
        name: 'Baseline',
        profile_id: 'baseline',
        config: { algorithm: 'baseline', time_limit: 1 },
      };

export function recommendedPlayer(
  models: Model[],
  profiles: AIProfile[],
  ruleset: string | undefined,
  fallback: TournamentPlayer,
): TournamentPlayer {
  const best = ruleset ? selectStrongestModel(models, ruleset)?.model : undefined;
  if (!best) return fallback;
  const profile = profiles.find((candidate) => candidate.algorithm === 'neural');
  return {
    name: best.name,
    profile_id: profile?.id ?? 'neural',
    config: {
      ...profile?.parameters,
      algorithm: 'neural',
      model_id: best.id,
      time_limit: 10,
    },
  };
}

/** Loading state and the shared refresh action for the model catalogue. */
export interface ModelCatalogueStatus {
  /** True while a catalogue request is in flight. */
  pending: boolean;
  /** Non-blocking failure message; any previous catalogue stays selectable. */
  error: string | null;
  /** Request the catalogue again. */
  refresh: () => void;
}

export interface AgentConfigFieldsProps {
  player: TournamentPlayer;
  /** Every registered model; the component filters by `ruleset` itself. */
  models: Model[];
  /** The single selected battlefield, or undefined when several are selected. */
  ruleset?: string;
  /** Namespace for aria-labels, e.g. "Opponent 1" or "Attackers". */
  prefix: string;
  /** Present when the caller can reload the catalogue on request. */
  catalogue?: ModelCatalogueStatus;
  /** Surface the strongest-model recommendation under the model selector. */
  showRecommendation?: boolean;
  /** Called before every edit so a parent can stop recommending this participant. */
  onEdit?: () => void;
  onChange: (player: TournamentPlayer) => void;
}

export default function AgentConfigFields({
  player,
  models,
  ruleset,
  prefix,
  catalogue,
  showRecommendation = false,
  onEdit,
  onChange,
}: AgentConfigFieldsProps) {
  const { t, locale } = useI18n();
  const modelsPending = catalogue?.pending ?? false;
  const compatible = ruleset ? models.filter((model) => model.ruleset === ruleset) : [];
  const recommendation = ruleset ? selectStrongestModel(models, ruleset) : null;
  const algorithm = String(player.config.algorithm ?? 'baseline');
  const neural = algorithm === 'neural';
  const wrapper = String(player.config.search_wrapper ?? 'mcts');
  const search = neural ? wrapper : algorithm;
  // A standalone alpha-beta agent and a neural opponent using the alpha-beta
  // wrapper expose the same optimizations; only a model-backed search loses the
  // symmetry switches, because a value head is not guaranteed D4-equivariant.
  const alphaBeta = algorithm === 'alpha_beta' || (neural && wrapper === 'alpha_beta');
  const searchSettings = searchFromConfig(player.config) ?? DEFAULT_ALPHA_BETA_SEARCH;
  const selectedModel = models.find((model) => model.id === player.config.model_id);
  const selectedCompatible = compatible.some((model) => model.id === player.config.model_id);
  // A model can stay selected after the battlefield changes; keep it visible and
  // named instead of silently dropping it from the list.
  const unavailableModel = !selectedCompatible && Boolean(player.config.model_id);
  const modelHint =
    ruleset && compatible.length === 0
      ? modelsPending
        ? t('Loading models…')
        : t('Train or import a model in the AI workshop first.')
      : t('Trained models require one matching battlefield.');

  const parameter = (key: string, value: string | number) => {
    onEdit?.();
    onChange({ ...player, config: { ...player.config, [key]: value } });
  };
  const updateSearch = (patch: Partial<AlphaBetaSearchSettings>) => {
    onEdit?.();
    const current = searchFromConfig(player.config) ?? DEFAULT_ALPHA_BETA_SEARCH;
    onChange({
      ...player,
      config: { ...player.config, search: effectiveSearch({ ...current, ...patch }) },
    });
  };
  const applySearchPreset = (name: AlphaBetaPresetName) => {
    onEdit?.();
    const current = searchFromConfig(player.config) ?? DEFAULT_ALPHA_BETA_SEARCH;
    onChange({
      ...player,
      config: { ...player.config, search: effectiveSearch(presetSearch(name, current)) },
    });
  };
  const changeAlgorithm = (next: string) => {
    onEdit?.();
    const config: Config = { ...player.config, algorithm: next };
    delete config.heuristic_version;
    delete config.heuristic;
    delete config.model_id;
    delete config.evaluation;
    delete config.search_wrapper;
    delete config.search;
    if (next === 'alpha_beta') config.search = { ...DEFAULT_ALPHA_BETA_SEARCH };
    if (next === 'neural' && ruleset)
      config.model_id = selectStrongestModel(models, ruleset)?.model.id ?? '';
    onChange({ ...player, profile_id: undefined, name: 'Computer', config });
  };
  const changeWrapper = (next: string) => {
    onEdit?.();
    const config: Config = { ...player.config, search_wrapper: next };
    // Nested alpha-beta settings are rejected for every other wrapper.
    if (next === 'alpha_beta' && !config.search) config.search = { ...DEFAULT_ALPHA_BETA_SEARCH };
    if (next !== 'alpha_beta') delete config.search;
    onChange({ ...player, config });
  };
  const field = (
    key: string,
    label: string,
    minimum: number,
    maximum: number,
    step = 1,
    fallback = minimum,
  ) => (
    <label key={key}>
      {t(label)}
      <input
        type="number"
        aria-label={`${prefix} ${t(label).toLocaleLowerCase(locale)}`}
        min={minimum}
        max={maximum}
        step={step}
        required
        value={Number(player.config[key] ?? fallback)}
        onChange={(event) => parameter(key, Number(event.target.value))}
      />
    </label>
  );

  return (
    <>
      {!player.profile_id && (
        <label>
          {t('Algorithm')}
          <select
            aria-label={`${prefix} ${t('Algorithm').toLocaleLowerCase(locale)}`}
            value={algorithm}
            onChange={(event) => changeAlgorithm(event.target.value)}
          >
            <option value="baseline">{t('Tactical heuristic')}</option>
            <option value="shield_maiden">{t('Shield maiden heuristic')}</option>
            <option value="alpha_beta">{t('Alpha-beta search')}</option>
            <option value="mcts">{t('Monte Carlo tree search')}</option>
            <option value="neural">{t('Neural network')}</option>
          </select>
        </label>
      )}
      {neural && (
        <label>
          {t('Trained model')}
          <select
            aria-label={`${prefix} ${t('trained model')}`}
            required
            value={String(player.config.model_id ?? '')}
            onChange={(event) => parameter('model_id', event.target.value)}
          >
            <option value="">{t('Select a compatible model')}</option>
            {compatible.map((model) => (
              <option value={model.id} key={model.id}>
                {modelOptionLabel(model, t)}
              </option>
            ))}
            {unavailableModel && (
              <option value={String(player.config.model_id)}>
                {selectedModel
                  ? `${selectedModel.name} · ${t('incompatible battlefield')}`
                  : t('Unavailable model')}
              </option>
            )}
          </select>
          <small>{modelHint}</small>
          {showRecommendation &&
            recommendation &&
            player.config.model_id === recommendation.model.id && (
              <small className="model-recommendation">
                {recommendation.basis === 'evaluation'
                  ? t(
                      'Best measured model selected: {name} ({score}% across {games} baseline games).',
                      {
                        name: recommendation.model.name,
                        score: ((recommendation.score ?? 0) * 100).toLocaleString(locale, {
                          minimumFractionDigits: 1,
                          maximumFractionDigits: 1,
                        }),
                        games: recommendation.games,
                      },
                    )
                  : t('No comparable evaluation; latest model selected: {name}.', {
                      name: recommendation.model.name,
                    })}
              </small>
            )}
        </label>
      )}
      {neural && catalogue && (
        <>
          {catalogue.error && (
            <p className="form-error" role="alert">
              {catalogue.error}
            </p>
          )}
          <button
            type="button"
            className="button secondary small"
            disabled={catalogue.pending}
            onClick={catalogue.refresh}
          >
            {catalogue.pending
              ? t('Refreshing models…')
              : catalogue.error
                ? t('Retry')
                : t('Refresh models')}
          </button>
        </>
      )}
      {selectedModel && neural && <ModelTrainingSummary model={selectedModel} compact />}
      {neural && (
        <SearchWrapper
          value={wrapper}
          label={`${prefix} ${t('Search wrapper').toLocaleLowerCase(locale)}`}
          onChange={changeWrapper}
        />
      )}
      {alphaBeta && (
        <AlphaBetaSearchFields
          prefix={prefix}
          modelBacked={neural}
          search={searchSettings}
          onPreset={applySearchPreset}
          onField={updateSearch}
        />
      )}
      {!neural && (search === 'alpha_beta' || search === 'mcts') && (
        <HeuristicSelect
          value={heuristicFromConfig(player.config)}
          label={`${prefix} ${t('Heuristic').toLocaleLowerCase(locale)}`}
          onChange={(next) => parameter('heuristic', next)}
        />
      )}
      {search !== 'none' && field('time_limit', 'Time limit (seconds)', 0.01, 15, 0.01, 0.1)}
      <details className="tournament-advanced">
        <summary>{t('Search parameters')}</summary>
        <div className="form-grid">
          {search === 'alpha_beta' && field('depth', 'Search depth', 1, 5, 1, 2)}
          {search === 'mcts' && (
            <>
              {field('iterations', 'Search iterations', 1, 5000, 1, 80)}
              {field('exploration', 'Exploration', 0, 10, 0.1, 1.4)}
              {algorithm === 'mcts' && field('rollout_depth', 'Rollout depth', 0, 50, 1, 8)}
            </>
          )}
          {(search === 'mcts' || (neural && search === 'none')) &&
            field('temperature', 'Temperature', 0, 5, 0.1, 0)}
          {algorithm === 'baseline' && (
            <p className="subtle">
              {t('A tactical heuristic with no additional search controls.')}
            </p>
          )}
        </div>
      </details>
    </>
  );
}
