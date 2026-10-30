import type { Dispatch, SetStateAction, FormEvent } from 'react';
import type { DeviceInfo, Model, Ruleset, TrainingWorker } from './types';
import { t } from './i18n';
import { sortRulesets } from './sortRulesets';
import { suggestVikingName } from './vikingNames';
import { changeTrainingPlan, type TrainingPreset, type TrainingConfig } from './trainingPresets';
import { numberField, SelfPlayModeField, ReplayPipelineField } from './TrainingFields';
import EvaluationSettings from './EvaluationSettings';
import TrainingTarget, { workerEligible } from './TrainingTarget';
import InferenceSettings, { inferenceTargetError } from './InferenceSettings';
import SymmetrySettings, { symmetryTargetError } from './SymmetrySettings';
import ConfigImportButton from './ConfigImportButton';
import TrainingDeviceField from './TrainingDeviceField';

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
}: Props) {
  const isRemote = kind === 'train' && target !== 'local';
  const selectedWorker = workers.find((worker) => `worker:${worker.id}` === target);
  const unavailableTarget =
    isRemote &&
    target.startsWith('worker:') &&
    (!selectedWorker || !workerEligible(selectedWorker));
  const targetDevice = isRemote ? selectedWorker?.capabilities : device;
  return (
    <>
      <p className="subtle">
        {t(
          'A policy and value network learns from its own games. Small runs verify the workflow; strength takes time and evaluation.',
        )}
      </p>
      <form onSubmit={start}>
        <div className="segmented">
          <button
            type="button"
            className={kind === 'train' ? 'active' : ''}
            onClick={() => setKind('train')}
          >
            {t('Train a model')}
          </button>
          <button
            type="button"
            className={kind === 'tune' ? 'active' : ''}
            onClick={() => {
              setKind('tune');
              update('evaluation_opponent', 'shield_bearer');
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
            <label>
              {t('Battlefield')}
              <select
                value={String(config.ruleset)}
                onChange={(e) =>
                  setConfig((previous) =>
                    changeTrainingPlan(
                      previous,
                      e.target.value,
                      String(previous.preset) as TrainingPreset,
                    ),
                  )
                }
              >
                {sortRulesets(rulesets).map((rule) => (
                  <option value={rule.id} key={rule.id}>
                    {rule.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t('Training preset')}
              <select
                value={String(config.preset)}
                onChange={(e) =>
                  setConfig((previous) =>
                    changeTrainingPlan(
                      previous,
                      String(previous.ruleset),
                      e.target.value as TrainingPreset,
                    ),
                  )
                }
              >
                <option value="recommended">
                  {t('Recommended · tailored to this battlefield')}
                </option>
                <option value="smoke">{t('Quick check · a tiny real training run')}</option>
                <option value="beginner">{t('Beginner · a starting point')}</option>
                <option value="balanced">{t('Balanced · a longer experiment')}</option>
              </select>
            </label>
            <div className="name-suggestion-field">
              <label htmlFor="training-model-name">{t('Model name')}</label>
              <div className="name-input-group">
                <input
                  id="training-model-name"
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
            />
            <InferenceSettings
              config={config}
              update={update}
              target={isRemote ? target : 'local'}
              workers={workers}
              local={device}
            />
            {numberField('iterations', config, update)}
          </div>
        </fieldset>
        <fieldset className="creation-group">
          <legend>{t('Neural network and optimization')}</legend>
          <div className="form-grid">
            {numberField('channels', config, update)}
            {numberField('blocks', config, update)}
            {numberField('batch_size', config, update)}
            {numberField('epochs', config, update)}
            {numberField('learning_rate', config, update)}
            <label>
              {t('Optimizer')}
              <select
                value={String(config.optimizer)}
                onChange={(e) => update('optimizer', e.target.value)}
              >
                <option value="adamw">AdamW</option>
                <option value="adam">Adam</option>
                <option value="sgd">SGD</option>
              </select>
            </label>
            <SymmetrySettings
              config={config}
              update={update}
              capabilities={
                isRemote
                  ? workers.find((worker) => `worker:${worker.id}` === target)?.capabilities
                  : device
              }
            />
          </div>
        </fieldset>
        <fieldset className="creation-group">
          <legend>{t('Self-play and MCTS')}</legend>
          <div className="form-grid">
            {numberField('games', config, update)}
            {numberField('mcts_iterations', config, update)}
            <ReplayPipelineField
              value={String(config.replay_pipeline ?? 'auto')}
              onChange={(value) => update('replay_pipeline', value)}
              disabled={busy}
            />
            <SelfPlayModeField
              value={String(config.self_play_mode)}
              onChange={(value) => update('self_play_mode', value)}
            />
            {numberField('self_play_concurrency', config, update)}
            {numberField('inference_batch_size', config, update)}
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
        <EvaluationSettings config={config} update={update} tuning={kind === 'tune'} />
        <label className="checkbox-label training-retain-best">
          <input
            type="checkbox"
            checked={Boolean(config.save_all_best)}
            onChange={(event) => update('save_all_best', event.target.checked)}
          />
          {t('Save every new best model')}
        </label>
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
            {numberField('mcts_time_limit', config, update)}
            {numberField('exploration', config, update)}
            {numberField('temperature', config, update)}
            {numberField('dirichlet_alpha', config, update)}
            {numberField('dirichlet_fraction', config, update)}
            {numberField('replay_buffer_size', config, update)}
            {numberField('weight_decay', config, update)}
            {config.optimizer === 'sgd' && numberField('momentum', config, update)}
            {numberField('checkpoint_frequency', config, update)}
            {numberField('max_game_plies', config, update)}
            {numberField('cpu_threads', config, update)}
            {numberField('search_workers', config, update)}
            {numberField('seed', config, update)}
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={Boolean(config.mixed_precision)}
                onChange={(e) => update('mixed_precision', e.target.checked)}
              />
              {t('Mixed precision when supported')}
            </label>
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
              <label>
                {t('Trial budget')}
                <input
                  type="number"
                  min="1"
                  max="10000"
                  required
                  value={trials}
                  onChange={(e) => setTrials(Number(e.target.value))}
                />
              </label>
              <label>
                {t('Study name')}
                <input
                  required
                  maxLength={100}
                  value={studyName}
                  onChange={(e) => setStudyName(e.target.value)}
                />
              </label>
              <label>
                {t('Study random seed')}
                <input
                  type="number"
                  required
                  min="0"
                  max="4294967295"
                  value={studySeed}
                  onChange={(event) => setStudySeed(event.currentTarget.valueAsNumber)}
                />
              </label>
            </div>
            <label>
              {t('Parameter search space (JSON)')}
              <textarea
                value={space}
                onChange={(e) => setSpace(e.target.value)}
                rows={7}
                spellCheck={false}
              />
            </label>
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
