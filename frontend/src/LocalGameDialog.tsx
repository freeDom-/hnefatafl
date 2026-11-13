import { useEffect, useState, type FormEvent } from 'react';
import { api, errorMessage } from './api';
import { useI18n } from './i18n';
import { navigate } from './navigation';
import Modal from './Modal';
import type { Game, Ruleset } from './types';

/** Seat labels are stored verbatim and shown next to the board. */
const MAX_LABEL = 40;

/**
 * Same-device pass-and-play creation. The creating account owns the record; the
 * server labels both seats and validates every move, and nothing here is rated.
 */
export default function LocalGameDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const [rulesets, setRulesets] = useState<Ruleset[]>([]);
  const [ruleset, setRuleset] = useState('');
  const [attackers, setAttackers] = useState('');
  const [defenders, setDefenders] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    api<Ruleset[]>('/rulesets', undefined, undefined, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setRulesets(value);
        setRuleset(
          value.some((rule) => rule.id === 'tablut') ? 'tablut' : (value[0]?.id ?? 'tablut'),
        );
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError('Rulesets unavailable. Refresh the page to recover.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);
  const create = (event: FormEvent) => {
    event.preventDefault();
    if (busy || loading || !rulesets.length) return;
    const labels = [attackers.trim(), defenders.trim()];
    if (labels.some((label) => !label)) {
      setError('Both local seats need a label.');
      return;
    }
    setError('');
    setBusy(true);
    void api<Game>('/games/local', {
      ruleset,
      attackers_label: labels[0],
      defenders_label: labels[1],
    })
      .then((game) => {
        onClose();
        navigate(`/games/${game.id}`);
      })
      .catch((failure) => setError(errorMessage(failure, 'The local game could not be created.')))
      .finally(() => setBusy(false));
  };
  return (
    <Modal title={t('Start a local 1v1 game')} onClose={onClose} initialFocus="select">
      <form onSubmit={create} className="new-game-form local-game-form">
        <p>{t('Pass and play on this device. Local games are never rated.')}</p>
        <fieldset className="form-grid" disabled={loading || busy || !rulesets.length}>
          <legend className="sr-only">{t('Local 1v1 settings')}</legend>
          <label>
            {t('Ruleset')}
            <select value={ruleset} onChange={(event) => setRuleset(event.target.value)}>
              {rulesets.map((rule) => (
                <option value={rule.id} key={rule.id}>
                  {t(rule.name)}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t('Attackers seat label')}
            <input
              value={attackers}
              maxLength={MAX_LABEL}
              placeholder={t('Player 1')}
              onChange={(event) => setAttackers(event.target.value)}
            />
          </label>
          <label>
            {t('Defenders seat label')}
            <input
              value={defenders}
              maxLength={MAX_LABEL}
              placeholder={t('Player 2')}
              onChange={(event) => setDefenders(event.target.value)}
            />
          </label>
        </fieldset>
        {loading && <p role="status">{t('Loading…')}</p>}
        {error && <p role="alert">{t(error)}</p>}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            {t('Cancel')}
          </button>
          <button
            className="button primary"
            disabled={busy || loading || !rulesets.length}
            aria-busy={busy}
          >
            {t('Start game')}
          </button>
        </div>
      </form>
    </Modal>
  );
}
