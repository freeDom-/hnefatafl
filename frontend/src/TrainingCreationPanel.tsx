import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  type Dispatch,
  type SetStateAction,
  type FormEvent,
} from 'react';
import type { DeviceInfo, Model, Ruleset, TrainingWorker } from './types';
import { t } from './i18n';
import { sortRulesets } from './sortRulesets';
import { suggestVikingName } from './vikingNames';
import { changeTrainingPlan, type TrainingPreset, type TrainingConfig } from './trainingPresets';
import {
  CheckboxField,
  fieldErrorAttributes,
  numberField,
  SelfPlayModeField,
  trainingParameterLabel,
} from './TrainingFields';
import { FieldLabel } from './FieldTooltip';
import { fieldIdentity } from './trainingPayload';
import EvaluationSettings from './EvaluationSettings';
import TrainingTarget, { workerEligible } from './TrainingTarget';
import InferenceSettings, { inferenceTargetError } from './InferenceSettings';
import SymmetrySettings, { symmetryTargetError } from './SymmetrySettings';
import ConfigImportButton from './ConfigImportButton';
import TrainingDeviceField from './TrainingDeviceField';

/**
 * Why a field the server refuses is refused. Known server-owned outputs get a
 * specific reason; anything else is explained generically so the technical name
 * is never shown without an action. Looked up by the full path first, then by the
 * form input it maps to.
 */
const FIELD_EXPLANATIONS: Record<string, string> = {
  output_dir: 'The server assigns each training job its own output directory.',
  registry_dir: 'The server keeps trained models in its own model directory.',
};

/** Study controls share their names with model settings, so they read differently. */
const FIELD_LABELS: Record<string, string> = {
  'study.trials': 'Trial budget',
  'study.study_name': 'Study name',
  'study.seed': 'Study random seed',
  'study.search_space': 'Parameter search space (JSON)',
};

/** Fields rendered inside the collapsed Advanced configuration section. */
const ADVANCED_NUMBER_FIELDS = [
  'mcts_time_limit',
  'exploration',
  'temperature',
  'dirichlet_alpha',
  'dirichlet_fraction',
  'replay_buffer_size',
  'weight_decay',
  'checkpoint_frequency',
  'max_game_plies',
  'cpu_threads',
  'search_workers',
  'seed',
];
// Rendered only while automatic hyperparameter updates are enabled.
const ADAPTATION_NUMBER_FIELDS = [
  'adaptation_cadence',
  'adaptation_warmup',
  'adaptation_min_dwell',
  'adaptation_lag',
  'adaptation_min_trials',
];
const ADVANCED_FIELDS = [
  ...ADVANCED_NUMBER_FIELDS,
  ...ADAPTATION_NUMBER_FIELDS,
  'adaptation_lr_candidates',
  'momentum',
];

interface Props {
  config: TrainingConfig;
  setConfig: Dispatch<SetStateAction<TrainingConfig>>;
  update: (key: string, value: string | number | boolean) => void;
  kind: 'train' | 'tune';
  setKind: Dispatch<SetStateAction<'train' | 'tune'>>;
  target: string;
  setTarget: Dispatch<SetStateAction<string>>;
  trials: number;
  setTrials: Dispatch<SetStateAction<number>>;
  studySeed: number;
  setStudySeed: Dispatch<SetStateAction<number>>;
  studyName: string;
  setStudyName: Dispatch<SetStateAction<string>>;
  space: string;
  setSpace: Dispatch<SetStateAction<string>>;
  strategy: string;
  setStrategy: Dispatch<SetStateAction<string>>;
  advanced: boolean;
  setAdvanced: Dispatch<SetStateAction<boolean>>;
  busy: boolean;
  models: Model[];
  rulesets: Ruleset[];
  workers: TrainingWorker[];
  device: DeviceInfo;
  start: (event: FormEvent) => void;
  importingConfig: boolean;
  onImportConfig: (file: File) => void;
  /** Full paths of the training fields the server rejected. */
  rejectedFields?: string[];
  /** Localized explanation for a request the form itself could not build. */
  rejectedReason?: string;
  /** Called with an input identity when the user edits that field. */
  onFieldCorrected?: (identity: string) => void;
  /** Called when the whole configuration is replaced (ruleset or preset). */
  onFieldsCleared?: () => void;
}

export default function TrainingCreationPanel({
  config,
  setConfig,
  update,
  kind,
  setKind,
  target,
  setTarget,
  trials,
  setTrials,
  studySeed,
  setStudySeed,
  studyName,
  setStudyName,
  space,
  setSpace,
  strategy,
  setStrategy,
  advanced,
  setAdvanced,
  busy,
  models,
  rulesets,
  workers,
  device,
  start,
  importingConfig,
  onImportConfig,
  rejectedFields = [],
  rejectedReason = '',
  onFieldCorrected,
  onFieldsCleared,
}: Props) {
  const isRemote = kind === 'train' && target !== 'local';
  const selectedWorker = workers.find((worker) => `worker:${worker.id}` === target);
  const unavailableTarget =
    isRemote &&
    target.startsWith('worker:') &&
    (!selectedWorker || !workerEligible(selectedWorker));
  const targetDevice = isRemote ? selectedWorker?.capabilities : device;
  const form = useRef<HTMLFormElement>(null);
  const alert = useRef<HTMLDivElement>(null);
  const alertId = useId();
  const rulesetId = useId();
  const presetId = useId();
  const modelNameId = useId();
  const optimizerId = useId();
  const adaptationId = useId();
  const trialsId = useId();
  const studyNameId = useId();
  const studySeedId = useId();
  const spaceId = useId();
  const strategyId = useId();
  // The form edits one nested configuration, so a rejected path maps to the input
  // that owns it. `training.seed` and `seed` stay distinct: the first is the model
  // seed, the second the Optuna study seed.
  const rejected = new Set(rejectedFields.map((path) => fieldIdentity(path, kind)));
  const invalid = (identity: string) => fieldErrorAttributes(rejected.has(identity), alertId);
  // A rejected field inside Advanced must be reachable instead of silently
  // collapsed, but unrelated sections are never touched.
  useEffect(() => {
    if (advanced) return;
    if (rejectedFields.some((path) => ADVANCED_FIELDS.includes(fieldIdentity(path, kind)))) {
      setAdvanced(true);
    }
  }, [advanced, rejectedFields, kind, setAdvanced]);
  const focusKey = rejectedFields.join('|');
  const focused = useRef('');
  const revealPending = useRef(false);
  // Focus the marked input once per rejection, and once more when our own
  // expansion reveals it. A manual toggle never moves focus. A layout effect
  // applies the focus within the commit that added the marked input, so the
  // guard cannot race with a user who moved focus in the meantime.
  useLayoutEffect(() => {
    if (!focusKey) {
      focused.current = '';
      revealPending.current = false;
      return;
    }
    const first = form.current?.querySelector<HTMLElement>('[aria-invalid="true"]') ?? null;
    if (focused.current === focusKey && !revealPending.current) return;
    revealPending.current = !first;
    focused.current = focusKey;
    (first ?? alert.current)?.focus();
  }, [focusKey, advanced]);
  return (
    <>
      <p className="subtle">
        {t(
          'A policy and value network learns from its own games. Small runs verify the workflow; strength takes time and evaluation.',
        )}
      </p>
      <form ref={form} onSubmit={start}>
        {!!rejectedFields.length && (
          <div
            className="form-error rejected-fields"
            role="alert"
            id={alertId}
            tabIndex={-1}
            ref={alert}
          >
            <p>
              {t(
                rejectedFields.length === 1
                  ? 'Training was not started: one configuration field was rejected.'
                  : 'Training was not started: {count} configuration fields were rejected.',
                { count: rejectedFields.length },
              )}
            </p>
            <ul>
              {rejectedFields.map((path) => {
                const identity = fieldIdentity(path, kind);
                const label = t(FIELD_LABELS[identity] ?? trainingParameterLabel(identity));
                return (
                  <li key={path}>
                    {label === path ? <code>{path}</code> : <strong>{label}</strong>}
                    {label !== path && (
                      <>
                        {' '}
                        (<code>{path}</code>)
                      </>
                    )}
                    {' — '}
                    {t(
                      FIELD_EXPLANATIONS[path] ??
                        FIELD_EXPLANATIONS[identity] ??
                        (rejectedReason ||
                          'This server does not accept this training field. Remove it from the configuration and try again.'),
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        <div className="segmented">
          <button
            type="button"
            className={kind === 'train' ? 'active' : ''}
            onClick={() => {
              // The same field name means a different setting per form, so a
              // rejection from the other form no longer applies.
              onFieldsCleared?.();
              setKind('train');
            }}
          >
            {t('Train a model')}
          </button>
          <button
            type="button"
            className={kind === 'tune' ? 'active' : ''}
            onClick={() => {
              onFieldsCleared?.();
              setKind('tune');
              update('evaluation_opponent', 'shield_bearer');
              // The controller and a tuning study both own the learning rate.
              update('adaptation', 'off');
            }}
          >
            {t('Tune parameters')}
          </button>
        </div>
        <div className="creation-import">
          <ConfigImportButton onImport={onImportConfig} busy={importingConfig} disabled={busy} />
          <span className="subtle">
            {t(
              'Load parameters from an exported config.json. Review them before starting; importing never starts a job.',
            )}
          </span>
        </div>
        <fieldset className="creation-group">
          <legend>{t('General')}</legend>
          <div className="form-grid">
            <TrainingTarget
              value={kind === 'tune' ? 'local' : target}
              workers={workers}
              disabled={kind === 'tune'}
              onChange={(value) => {
                setTarget(value);
                if (config.inference_backend !== 'openvino') update('device', 'auto');
              }}
            />
            <div className="training-field">
              <FieldLabel htmlFor={rulesetId} label="Battlefield" tooltipKey="ruleset" />
              <select
                id={rulesetId}
                data-training-field="ruleset"
                {...invalid('ruleset')}
                value={String(config.ruleset)}
                onChange={(e) => {
                  onFieldsCleared?.();
                  setConfig((previous) =>
                    changeTrainingPlan(
                      previous,
                      e.target.value,
                      String(previous.preset) as TrainingPreset,
                    ),
                  );
                }}
              >
                {sortRulesets(rulesets).map((rule) => (
                  <option value={rule.id} key={rule.id}>
                    {rule.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="training-field">
              <FieldLabel htmlFor={presetId} label="Training preset" tooltipKey="preset" />
              <select
                id={presetId}
                data-training-field="preset"
                {...invalid('preset')}
                value={String(config.preset)}
                onChange={(e) => {
                  onFieldsCleared?.();
                  setConfig((previous) =>
                    changeTrainingPlan(
                      previous,
                      String(previous.ruleset),
                      e.target.value as TrainingPreset,
                    ),
                  );
                }}
              >
                <option value="recommended">
                  {t('Recommended · tailored to this battlefield')}
                </option>
                <option value="smoke">{t('Quick check · a tiny real training run')}</option>
                <option value="beginner">{t('Beginner · a starting point')}</option>
                <option value="balanced">{t('Balanced · a longer experiment')}</option>
              </select>
            </div>
            <div className="training-field name-suggestion-field">
              <FieldLabel htmlFor={modelNameId} label="Model name" tooltipKey="model_name" />
              <div className="name-input-group">
                <input
                  id={modelNameId}
                  data-training-field="model_name"
                  {...invalid('model_name')}
                  required
                  maxLength={100}
                  value={String(config.model_name)}
                  onChange={(e) => update('model_name', e.target.value)}
                />
                <button
                  type="button"
                  className="icon-button"
                  aria-label={t('Suggest Viking name')}
                  title={t('Suggest a Viking name')}
                  onClick={() =>
                    update(
                      'model_name',
                      suggestVikingName([
                        ...models.map((model) => model.name),
                        String(config.model_name),
                      ]),
                    )
                  }
                >
                  ⚄
                </button>
              </div>
            </div>
            <TrainingDeviceField
              value={String(config.device)}
              onChange={(value) => update('device', value)}
              remote={isRemote}
              capabilities={targetDevice}
              invalid={rejected.has('device')}
              errorId={alertId}
            />
            <InferenceSettings
              config={config}
              update={update}
              target={isRemote ? target : 'local'}
              workers={workers}
              local={device}
              invalidFields={[...rejected]}
              errorId={alertId}
            />
            {numberField('iterations', config, update, {
              invalid: rejected.has('iterations'),
              errorId: alertId,
            })}
          </div>
        </fieldset>
        <fieldset className="creation-group">
          <legend>{t('Neural network and optimization')}</legend>
          <div className="form-grid">
            {numberField('channels', config, update, {
              invalid: rejected.has('channels'),
              errorId: alertId,
            })}
            {numberField('blocks', config, update, {
              invalid: rejected.has('blocks'),
              errorId: alertId,
            })}
            {numberField('batch_size', config, update, {
              invalid: rejected.has('batch_size'),
              errorId: alertId,
            })}
            {numberField('epochs', config, update, {
              invalid: rejected.has('epochs'),
              errorId: alertId,
            })}
            {numberField('learning_rate', config, update, {
              invalid: rejected.has('learning_rate'),
              errorId: alertId,
            })}
            <div className="training-field">
              <FieldLabel htmlFor={optimizerId} label="Optimizer" tooltipKey="optimizer" />
              <select
                id={optimizerId}
                data-training-field="optimizer"
                {...invalid('optimizer')}
                value={String(config.optimizer)}
                onChange={(e) => update('optimizer', e.target.value)}
              >
                <option value="adamw">AdamW</option>
                <option value="adam">Adam</option>
                <option value="sgd">SGD</option>
              </select>
            </div>
            <div className="training-field">
              <FieldLabel
                htmlFor={adaptationId}
                label="Automatic hyperparameter updates"
                tooltipKey="adaptation"
              />
              <select
                id={adaptationId}
                data-training-field="adaptation"
                {...invalid('adaptation')}
                value={kind === 'tune' ? 'off' : String(config.adaptation)}
                disabled={kind === 'tune'}
                onChange={(e) => update('adaptation', e.target.value)}
              >
                <option value="off">Off</option>
                <option value="hypercontroller">HyperController</option>
              </select>
              {kind === 'tune' && (
                <small>
                  {t('Unavailable while tuning: the study varies the learning rate across trials.')}
                </small>
              )}
            </div>
            <SymmetrySettings
              config={config}
              update={update}
              capabilities={
                isRemote
                  ? workers.find((worker) => `worker:${worker.id}` === target)?.capabilities
                  : device
              }
              invalid={rejected.has('training_symmetry_mode')}
              errorId={alertId}
            />{' '}
          </div>
        </fieldset>
        <fieldset className="creation-group">
          <legend>{t('Self-play and MCTS')}</legend>
          <div className="form-grid">
            {numberField('games', config, update, {
              invalid: rejected.has('games'),
              errorId: alertId,
            })}
            {numberField('mcts_iterations', config, update, {
              invalid: rejected.has('mcts_iterations'),
              errorId: alertId,
            })}
            <SelfPlayModeField
              value={String(config.self_play_mode)}
              onChange={(value) => update('self_play_mode', value)}
              invalid={rejected.has('self_play_mode')}
              errorId={alertId}
            />
            {numberField('self_play_concurrency', config, update, {
              invalid: rejected.has('self_play_concurrency'),
              errorId: alertId,
            })}
            {numberField('inference_batch_size', config, update, {
              invalid: rejected.has('inference_batch_size'),
              errorId: alertId,
            })}
          </div>
        </fieldset>
        {kind === 'tune' && (
          <p className="subtle">
            {t('Parameter tuning runs on this server. Remote workers support model training.')}
          </p>
        )}
        {isRemote && (
          <p className="subtle">
            {t(
              'Jobs wait in the queue until a compatible worker has capacity. You can queue several runs with different settings.',
            )}
          </p>
        )}
        {config.preset === 'recommended' && (
          <p className="training-plan-note subtle">
            {Number(config.channels)} {t('channels ·')} {Number(config.blocks)}{' '}
            {t('residual blocks ·')} {Number(config.games) * Number(config.iterations)}{' '}
            {t('self-play games.')}{' '}
            {t(
              'A board-specific starting budget, not a proven strongest configuration. Full runs can take hours or days; choose Quick check to verify your setup.',
            )}
          </p>
        )}
        <EvaluationSettings
          models={models}
          strategy={strategy}
          onStrategyChange={kind === 'tune' ? setStrategy : undefined}
          config={config}
          update={update}
          tuning={kind === 'tune'}
          invalidFields={[...rejected]}
          errorId={alertId}
        />
        <CheckboxField
          fieldKey="save_all_best"
          label="Save every new best model"
          className="training-retain-best"
          checked={Boolean(config.save_all_best)}
          invalid={rejected.has('save_all_best')}
          errorId={alertId}
          onChange={(checked) => update('save_all_best', checked)}
        />
        <p className="subtle">
          {t(
            'Retain and register each strict evaluation improvement. Tied scores do not create extra models. Uses additional disk space.',
          )}
        </p>
        <button
          type="button"
          className="text-button advanced-toggle"
          onClick={() => setAdvanced((value) => !value)}
          aria-expanded={advanced}
        >
          {advanced ? '−' : '＋'} {t('Advanced configuration')}
        </button>
        {advanced && (
          <div className="advanced-config form-grid">
            {ADVANCED_NUMBER_FIELDS.map((key) =>
              numberField(key, config, update, {
                invalid: rejected.has(key),
                errorId: alertId,
              }),
            )}
            {config.optimizer === 'sgd' &&
              numberField('momentum', config, update, {
                invalid: rejected.has('momentum'),
                errorId: alertId,
              })}
            {config.adaptation === 'hypercontroller' && (
              <div className="training-field">
                <FieldLabel
                  htmlFor={`${adaptationId}-candidates`}
                  label="Learning rate candidates"
                  tooltipKey="adaptation_lr_candidates"
                />
                <input
                  id={`${adaptationId}-candidates`}
                  type="text"
                  data-training-field="adaptation_lr_candidates"
                  {...invalid('adaptation_lr_candidates')}
                  value={String(config.adaptation_lr_candidates)}
                  onChange={(e) => update('adaptation_lr_candidates', e.target.value)}
                />
              </div>
            )}
            {config.adaptation === 'hypercontroller' &&
              ADAPTATION_NUMBER_FIELDS.map((key) =>
                numberField(key, config, update, {
                  invalid: rejected.has(key),
                  errorId: alertId,
                }),
              )}
            <CheckboxField
              fieldKey="mixed_precision"
              label="Mixed precision when supported"
              checked={Boolean(config.mixed_precision)}
              invalid={rejected.has('mixed_precision')}
              errorId={alertId}
              onChange={(checked) => update('mixed_precision', checked)}
            />
          </div>
        )}
        {kind === 'tune' && (
          <div className="tuning-config">
            <p className="subtle">
              {t(
                'Trials optimize game results against a baseline. Tuning can take much longer than a single run. Resume a saved study from its job below.',
              )}
            </p>
            <div className="form-grid">
              <div className="training-field">
                <FieldLabel htmlFor={trialsId} label="Trial budget" tooltipKey="study.trials" />
                <input
                  id={trialsId}
                  type="number"
                  data-training-field="study.trials"
                  {...invalid('study.trials')}
                  min="1"
                  max="10000"
                  required
                  value={trials}
                  onChange={(e) => {
                    setTrials(Number(e.target.value));
                    onFieldCorrected?.('study.trials');
                  }}
                />
              </div>
              <div className="training-field">
                <FieldLabel
                  htmlFor={studyNameId}
                  label="Study name"
                  tooltipKey="study.study_name"
                />
                <input
                  id={studyNameId}
                  data-training-field="study.study_name"
                  {...invalid('study.study_name')}
                  required
                  maxLength={100}
                  value={studyName}
                  onChange={(e) => {
                    setStudyName(e.target.value);
                    onFieldCorrected?.('study.study_name');
                  }}
                />
              </div>
              <div className="training-field">
                <FieldLabel
                  htmlFor={studySeedId}
                  label="Study random seed"
                  tooltipKey="study.seed"
                />
                <input
                  id={studySeedId}
                  type="number"
                  data-training-field="study.seed"
                  {...invalid('study.seed')}
                  required
                  min="0"
                  max="4294967295"
                  value={studySeed}
                  onChange={(event) => {
                    setStudySeed(event.currentTarget.valueAsNumber);
                    onFieldCorrected?.('study.seed');
                  }}
                />
              </div>
            </div>
            <div className="training-field">
              <FieldLabel
                htmlFor={spaceId}
                label="Parameter search space (JSON)"
                tooltipKey="study.search_space"
              />
              <textarea
                id={spaceId}
                data-training-field="study.search_space"
                {...invalid('study.search_space')}
                value={space}
                onChange={(e) => {
                  setSpace(e.target.value);
                  onFieldCorrected?.('study.search_space');
                }}
                rows={7}
                spellCheck={false}
              />
            </div>
            <div className="training-field">
              <FieldLabel
                htmlFor={strategyId}
                label="Optional study settings (JSON)"
                tooltipKey="strategy"
              />
              <textarea
                id={strategyId}
                data-training-field="strategy"
                value={strategy}
                placeholder={
                  '{\n  "opponents": [{"opponent": "jarl", "weight": 1}],\n  "verification": {"games": 8, "opponent": "shield_bearer"},\n  "max_failures": 3\n}'
                }
                onChange={(e) => setStrategy(e.target.value)}
                rows={5}
                spellCheck={false}
              />
              <small className="subtle">
                {t(
                  'Optional opponents, held-out verification, sampler/pruner startup and failure budget. Leave empty for the default single-opponent objective.',
                )}
              </small>
            </div>
          </div>
        )}
        <div className="training-start">
          <span>{t('Start small. Measure improvement.')}</span>
          <button
            className="button primary"
            disabled={
              busy ||
              unavailableTarget ||
              Boolean(inferenceTargetError(config, isRemote ? target : 'local', targetDevice)) ||
              Boolean(
                symmetryTargetError(
                  config,
                  isRemote
                    ? workers.find((worker) => `worker:${worker.id}` === target)?.capabilities
                    : device,
                ),
              )
            }
          >
            {busy
              ? t('Starting…')
              : kind === 'train'
                ? isRemote
                  ? t('Queue training')
                  : t('Start training')
                : t('Start tuning')}{' '}
            <span aria-hidden="true">↗</span>
          </button>
        </div>
      </form>
    </>
  );
}
