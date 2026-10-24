import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import RecordsConfirm from './RecordsConfirm';
import { useI18n } from './i18n';
import type { TrainingWorker } from './types';
import './trainingWorkers.css';

export default function TrainingWorkers({
  workers,
  onChanged,
}: {
  workers: TrainingWorker[];
  onChanged: (revokedId?: string) => void;
}) {
  const { t, locale } = useI18n();
  const { account } = useAuth();
  const [connect, setConnect] = useState(false);
  const [revoking, setRevoking] = useState<TrainingWorker | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revokedIds, setRevokedIds] = useState<string[]>([]);
  const [overriding, setOverriding] = useState<TrainingWorker | null>(null);
  const changeOverride = async () => {
    if (!overriding) return;
    setBusy(true);
    setError('');
    try {
      await api(
        `/training/workers/${overriding.id}/version-override`,
        {
          enabled: !overriding.version_override_enabled,
        },
        'PATCH',
      );
      setOverriding(null);
      onChanged();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const visibleWorkers = workers.filter(
    (worker) => worker.status !== 'revoked' && !revokedIds.includes(worker.id),
  );
  const revoke = async () => {
    if (!revoking) return;
    setBusy(true);
    setError('');
    try {
      await api(`/training/workers/${revoking.id}`, undefined, 'DELETE');
      setRevokedIds((previous) => [...previous, revoking.id]);
      setRevoking(null);
      onChanged(revoking.id);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel training-workers">
      <div className="panel-title">
        <div>
          <h2>{t('Remote workers')}</h2>
          <p className="subtle">
            {t(
              'Connect cloud machines to train several models at the same time. Progress and models return here.',
            )}
          </p>
        </div>
        <button className="button secondary small" type="button" onClick={() => setConnect(true)}>
          {t('Connect a worker')}
        </button>
      </div>
      {visibleWorkers.length ? (
        <div className="workers-list">
          {visibleWorkers.map((worker) => (
            <article key={worker.id} className="worker-card" data-worker-id={worker.id}>
              <div className="worker-heading">
                <h3>{worker.name}</h3>
                <span className={`worker-status ${worker.status}`}>{t(worker.status)}</span>
              </div>
              <p
                className={`worker-compatibility ${worker.compatibility?.code ?? 'unknown_application_version'}`}
              >
                {t(worker.compatibility?.reason ?? 'Missing or unknown application version')}
              </p>
              <p>
                {t('Worker version')}: {worker.compatibility?.worker_version ?? t('Unknown')}
                {' · '}
                {t('Server version')}: {worker.compatibility?.server_version ?? t('Unknown')}
              </p>
              {(worker.compatibility?.worker_git_revision ||
                worker.compatibility?.server_git_revision) && (
                <p className="subtle">
                  {t('Worker commit')}: {worker.compatibility?.worker_git_revision ?? t('Unknown')}
                  {' · '}
                  {t('Server commit')}: {worker.compatibility?.server_git_revision ?? t('Unknown')}
                </p>
              )}
              <p className={worker.version_override_enabled ? 'worker-override-warning' : 'subtle'}>
                {t(
                  worker.version_override_enabled
                    ? 'Version override enabled'
                    : 'Version override disabled',
                )}
                {worker.compatibility?.override_active &&
                  ` · ${t('Unsupported version combination')}`}
              </p>
              {account?.role === 'ADMIN' && (
                <button
                  type="button"
                  className="text-button"
                  disabled={busy}
                  aria-label={t(
                    worker.version_override_enabled
                      ? 'Disable version override for {name}'
                      : 'Enable version override for {name}',
                    { name: worker.name },
                  )}
                  onClick={() => {
                    setError('');
                    setOverriding(worker);
                  }}
                >
                  {t(
                    worker.version_override_enabled
                      ? 'Disable version override'
                      : 'Enable version override',
                  )}
                </button>
              )}
              <p>
                {t('{active} / {capacity} jobs running', {
                  active: worker.active_jobs,
                  capacity: worker.capacity,
                })}
              </p>
              <p className="subtle">
                {worker.capabilities.cuda_available
                  ? t('NVIDIA CUDA')
                  : worker.capabilities.mps_available
                    ? t('Apple Metal')
                    : worker.last_seen
                      ? t('CPU')
                      : t('Waiting for the first connection')}
              </p>
              {!!worker.capabilities.cuda_devices?.length && (
                <p className="subtle">{worker.capabilities.cuda_devices.join(' · ')}</p>
              )}
              {worker.capabilities.openvino_version && (
                <p className="subtle">
                  OpenVINO {worker.capabilities.openvino_version} ·{' '}
                  {worker.capabilities.openvino_devices
                    ?.filter((device) => device.available)
                    .map((device) => `${device.id}: ${device.name}`)
                    .join(' · ') || t('unavailable')}
                </p>
              )}
              {worker.capabilities.openvino_error && (
                <p className="subtle">{worker.capabilities.openvino_error}</p>
              )}
              <small>
                {worker.last_seen
                  ? t('Last seen {time}', {
                      time: new Date(worker.last_seen).toLocaleString(locale),
                    })
                  : t('Not connected yet')}
              </small>
              <button
                type="button"
                className="text-button danger-text"
                aria-label={t('Revoke worker {name}', { name: worker.name })}
                onClick={() => {
                  setError('');
                  setRevoking(worker);
                }}
              >
                {t('Revoke access')}
              </button>
            </article>
          ))}
        </div>
      ) : (
        <p className="subtle">
          {t(
            'No workers connected. Create a worker credential, then run the worker command on your cloud machine.',
          )}
        </p>
      )}
      {connect && <WorkerSetup onClose={() => setConnect(false)} onCreated={onChanged} />}
      {overriding && (
        <RecordsConfirm
          title={t(
            overriding.version_override_enabled
              ? 'Disable version override'
              : 'Enable version override',
          )}
          busy={busy}
          error={error}
          confirmLabel={t('Apply')}
          busyLabel="Working…"
          cancelLabel="Cancel"
          onCancel={() => setOverriding(null)}
          onConfirm={() => void changeOverride()}
        >
          <p>
            {t(
              'Different software versions may produce inconsistent training behavior or invalid benchmark comparisons. Protocol and hardware checks still apply. Existing running jobs keep their leases.',
            )}
          </p>
        </RecordsConfirm>
      )}
      {revoking && (
        <RecordsConfirm
          title={t('Revoke worker {name}?', { name: revoking.name })}
          busy={busy}
          error={error}
          onCancel={() => setRevoking(null)}
          onConfirm={() => void revoke()}
        >
          <p>
            {t(
              'This credential will stop working. Assigned running jobs will stop accepting updates and keep their last uploaded checkpoint. Queued jobs assigned to this worker are cancelled.',
            )}
          </p>
        </RecordsConfirm>
      )}
    </section>
  );
}

function WorkerSetup({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { t } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState('');
  const [capacity, setCapacity] = useState(1);
  const [created, setCreated] = useState<{ worker: TrainingWorker; token: string } | null>(null);
  const [showToken, setShowToken] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  const create = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      setCreated(await api('/training/workers', { name: name.trim(), capacity }));
      onCreated();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const command = `.venv/bin/python -m hnefatafl.cli worker --server ${window.location.origin} --work-dir ./data/remote-worker${created && created.worker.capacity > 1 ? ` --concurrency ${created.worker.capacity}` : ''}`;
  return (
    <dialog
      ref={dialog}
      className="modal worker-setup-dialog"
      aria-label={t('Connect a remote worker')}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-top">
        <h2>{t('Connect a remote worker')}</h2>
        <button
          className="icon-button"
          type="button"
          aria-label={t('Close worker setup')}
          disabled={busy}
          onClick={onClose}
        >
          ×
        </button>
      </div>
      {!created ? (
        <form onSubmit={create}>
          <p className="subtle">
            {t(
              'Give this machine a name and choose how many independent training jobs it may run.',
            )}
          </p>
          <div className="form-grid">
            <label>
              {t('Worker name')}
              <input
                required
                maxLength={80}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label>
              {t('Parallel jobs')}
              <input
                required
                type="number"
                min={1}
                max={16}
                step={1}
                value={capacity}
                onChange={(event) => setCapacity(Number(event.target.value))}
              />
            </label>
          </div>
          <div className="modal-actions">
            <button className="button secondary" type="button" disabled={busy} onClick={onClose}>
              {t('Cancel')}
            </button>
            <button className="button primary" type="submit" disabled={busy || !name.trim()}>
              {busy ? t('Creating…') : t('Create worker')}
            </button>
          </div>
        </form>
      ) : (
        <div className="worker-instructions">
          <p>
            {t(
              'Install this version of Hnefatafl with training dependencies on the cloud machine. Use its CPU or install the matching PyTorch build for its GPU.',
            )}
          </p>
          <label>
            {t('Worker token')}
            <input
              readOnly
              type={showToken ? 'text' : 'password'}
              autoComplete="off"
              value={created.token}
            />
          </label>
          <div className="worker-token-actions">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={showToken}
                onChange={(event) => setShowToken(event.target.checked)}
              />
              {t('Show token')}
            </label>
            <button
              className="button secondary small"
              type="button"
              onClick={() => {
                if (!navigator.clipboard) {
                  setError(t('Copy failed. Reveal and copy the token manually.'));
                  return;
                }
                void navigator.clipboard
                  .writeText(created.token)
                  .then(() => setCopied(true))
                  .catch(() => setError(t('Copy failed. Reveal and copy the token manually.')));
              }}
            >
              {copied ? t('Copied') : t('Copy token')}
            </button>
          </div>
          <p className="subtle">
            {t(
              'Save this token now. It is shown only once and is not saved in this browser. Keep it private.',
            )}
          </p>
          <p>
            {t(
              'On the worker, read the token into an environment variable, then start the client:',
            )}
          </p>
          <pre className="log-view worker-command">{`read -r -s HNEFATAFL_WORKER_TOKEN\nexport HNEFATAFL_WORKER_TOKEN\n${command}`}</pre>
          <p className="subtle">
            {t(
              'The worker must reach this server URL through your private network or HTTPS proxy. It only makes outbound requests; no worker port needs to be opened.',
            )}
          </p>
          <div className="modal-actions">
            <button className="button primary" type="button" onClick={onClose}>
              {t('Done')}
            </button>
          </div>
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {t(error)}
        </p>
      )}
    </dialog>
  );
}
