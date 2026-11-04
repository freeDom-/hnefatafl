import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { api, errorMessage } from './api';
import { notify } from './notifications';
import { useI18n } from './i18n';
import type { Job, Model } from './types';
import { FieldLabel } from './FieldTooltip';
import EvaluationSettings from './EvaluationSettings';
import type { TrainingConfig } from './trainingPresets';
import type { ResumeCheckpoint } from './TrainingResumeDialog';
import { parseStudyStrategy, parseStudySpace, TrainingInputError } from './trainingPayload';

const DEFAULT_SPACE =
  '{\n  "learning_rate": {"type": "float", "low": 0.0001, "high": 0.003, "log": true},\n  "mcts_iterations": {"type": "int", "low": 4, "high": 16}\n}';

/**
 * Reviews and starts a checkpoint-based tuning study. Every trial resumes the
 * same full checkpoint for a target of `completed + additional` iterations, so
 * the user configures the extra budget explicitly instead of inheriting the
 * source job's already-completed total.
 */
export default function TrainingTuneDialog({
  job,
  models = [],
  onClose,
  onStarted,
}: {
  job: Job;
  models?: Model[];
  onClose: () => void;
  onStarted: (next: Job) => void;
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const [checkpoint, setCheckpoint] = useState<ResumeCheckpoint | null>(null);
  const [training, setTraining] = useState<TrainingConfig>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [additional, setAdditional] = useState(5);
  const [trials, setTrials] = useState(3);
  const [studyName, setStudyName] = useState('hnefatafl-search');
  const [seed, setSeed] = useState(0);
  const [space, setSpace] = useState(DEFAULT_SPACE);
  const [strategy, setStrategy] = useState('');
  const additionalId = useId();
  const trialsId = useId();
  const studyNameId = useId();
  const seedId = useId();
  const spaceId = useId();
  const strategyId = useId();
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    void api<ResumeCheckpoint>(`/training/jobs/${job.id}/resume`)
      .then((info) => {
        if (!alive) return;
        setCheckpoint(info);
        const saved: TrainingConfig = {};
        for (const [key, value] of Object.entries({ ...job.config, ...info.config })) {
          if (['string', 'number', 'boolean'].includes(typeof value)) {
            saved[key] = value as string | number | boolean;
          }
        }
        saved.ruleset = info.ruleset;
        saved.channels = info.channels;
        saved.blocks = info.blocks;
        saved.optimizer = info.optimizer;
        if (info.seed !== undefined) saved.seed = info.seed;
        saved.model_name = info.model_name;
        setTraining(saved);
      })
      .catch((error) => {
        if (alive) notify.error(errorMessage(error));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [job]);
  const completed = checkpoint?.completed_iteration ?? 0;
  const target = completed + additional;
  const valid =
    !!checkpoint?.can_resume &&
    training.evaluation_opponent !== 'champion' &&
    Number.isInteger(additional) &&
    additional >= 1 &&
    target <= 100000 &&
    Number.isInteger(trials) &&
    trials >= 1 &&
    trials <= 10000 &&
    typeof studyName === 'string' &&
    studyName.trim().length >= 1 &&
    studyName.length <= 100 &&
    Number.isInteger(seed) &&
    seed >= 0 &&
    seed <= 4294967295;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!checkpoint || !valid) return;
    setBusy(true);
    try {
      const payload = {
        kind: 'tune',
        trials,
        study_name: studyName,
        seed,
        search_space: parseStudySpace(space),
        // A portable token, so the dependency survives archive export/import.
        source_checkpoint: `${job.id}:last.pt`,
        ...parseStudyStrategy(strategy),
        training: {
          ...training,
          iterations: target,
        },
      };
      const next = await api<Job>('/training/jobs', payload);
      onStarted(next);
    } catch (error) {
      if (error instanceof TrainingInputError) notify.error(error.message);
      else notify.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <dialog
      ref={ref}
      className="modal training-resume-dialog"
      aria-label={t('Tune this checkpoint')}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form onSubmit={submit}>
        <div className="modal-top">
          <div>
            <h2>{t('Tune this checkpoint')}</h2>
            <p>{checkpoint?.model_name ?? String(job.config?.model_name ?? t('Training job'))}</p>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label={t('Close tuning settings')}
            disabled={busy}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        {loading ? (
          <p role="status">{t('Checking the saved training checkpoint…')}</p>
        ) : checkpoint && checkpoint.can_resume ? (
          <>
            <p className="subtle">
              {t(
                'Every trial resumes this full checkpoint for the same additional budget. The source checkpoint is never modified.',
              )}
            </p>
            <div className="form-grid">
              <div className="training-field">
                <FieldLabel
                  htmlFor={additionalId}
                  label="Additional tuning iterations per trial"
                  tooltipKey="additional_tuning_iterations"
                />
                <input
                  id={additionalId}
                  type="number"
                  data-training-field="additional_tuning_iterations"
                  min="1"
                  max="100000"
                  required
                  value={additional}
                  disabled={busy}
                  onChange={(event) => setAdditional(Number(event.target.value))}
                />
                <small>
                  {t(
                    'Each trial targets {target} total iterations ({completed} already completed).',
                    {
                      target,
                      completed,
                    },
                  )}
                </small>
              </div>
              <div className="training-field">
                <FieldLabel htmlFor={trialsId} label="Trial budget" tooltipKey="study.trials" />
                <input
                  id={trialsId}
                  type="number"
                  data-training-field="study.trials"
                  min="1"
                  max="10000"
                  required
                  value={trials}
                  disabled={busy}
                  onChange={(event) => setTrials(Number(event.target.value))}
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
                  required
                  maxLength={100}
                  value={studyName}
                  disabled={busy}
                  onChange={(event) => setStudyName(event.target.value)}
                />
              </div>
              <div className="training-field">
                <FieldLabel htmlFor={seedId} label="Study random seed" tooltipKey="study.seed" />
                <input
                  id={seedId}
                  type="number"
                  data-training-field="study.seed"
                  required
                  min="0"
                  max="4294967295"
                  value={seed}
                  disabled={busy}
                  onChange={(event) => setSeed(event.target.valueAsNumber)}
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
                value={space}
                disabled={busy}
                rows={6}
                spellCheck={false}
                onChange={(event) => setSpace(event.target.value)}
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
                disabled={busy}
                rows={4}
                spellCheck={false}
                onChange={(event) => setStrategy(event.target.value)}
              />
            </div>
            <EvaluationSettings
              tuning
              models={models}
              strategy={strategy}
              onStrategyChange={setStrategy}
              config={training}
              update={(key, value) => setTraining((previous) => ({ ...previous, [key]: value }))}
              disabled={busy}
            />
          </>
        ) : (
          <p className="form-error" role="alert">
            {t(checkpoint?.reason ?? 'No resumable full training checkpoint is available.')}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" disabled={busy} onClick={onClose}>
            {t('Cancel')}
          </button>
          <button type="submit" className="button primary" disabled={loading || busy || !valid}>
            {busy ? t('Starting…') : t('Start tuning')}
          </button>
        </div>
      </form>
    </dialog>
  );
}
