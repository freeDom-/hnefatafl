import { useEffect, useRef, useState, type FormEvent } from 'react';
import Board from './Board';
import Modal from './Modal';
import { AppearanceSettings } from './Appearance';
import { LanguageSelect, useI18n } from './i18n';
import { GuestEngine } from './guestEngine';
import { GuestConflict, readGuestSlot, writeGuestSlot, clearGuestSlot } from './guestStore';
import { parseGuestRecord, type GuestInfo, type GuestSnapshot } from './guestTypes';
import { surfaceLink } from './surfaceNavigation';
import type { Side } from './types';
import './guest.css';
const sides = { attackers: 'Attackers', defenders: 'Defenders' };
export default function Guest() {
  const { t } = useI18n();
  const [info, setInfo] = useState<GuestInfo | null>(null);
  const [game, setGame] = useState<GuestSnapshot | null>(null);
  const current = useRef<GuestSnapshot | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(true);
  const lock = useRef(false);
  const alive = useRef(false);
  const engine = useRef<GuestEngine | null>(null);
  const revision = useRef<string | null | undefined>(undefined);
  const [hasSlot, setHasSlot] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [warning, setWarning] = useState('');
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<number | null>(null);
  const [rule, setRule] = useState('brandubh');
  const [side, setSide] = useState<Side>('attackers');
  const [confirm, setConfirm] = useState<'replace' | 'clear' | 'reload' | null>(null);
  const operation = useRef(0);
  const publish = (value: GuestSnapshot) => {
    current.current = value;
    setGame(value);
    setSelected(null);
  };
  const boot = async (fromDevice = true) => {
    if (lock.current) return;
    lock.current = true;
    const expected = ++operation.current;
    setBusy(true);
    setReady(false);
    setError('');
    setSelected(null);
    engine.current?.dispose();
    const runtime = new GuestEngine();
    engine.current = runtime;
    try {
      let saved: unknown | null = null;
      if (fromDevice) {
        try {
          const slot = await readGuestSlot();
          if (!alive.current || expected !== operation.current) return;
          revision.current = slot.revision;
          saved = slot.raw;
          if (alive.current && expected === operation.current) {
            setHasSlot(slot.raw !== null);
            setConflict(false);
          }
        } catch {
          if (!alive.current || expected !== operation.current) return;
          revision.current = undefined;
          if (alive.current && expected === operation.current) {
            setHasSlot(true);
            setWarning(
              'Device storage is unavailable. Play can continue in memory; changes may be lost on reload.',
            );
          }
        }
      } else saved = current.current ? JSON.stringify(current.current.record) : null;
      await runtime.start();
      const catalog = await runtime.request<GuestInfo>({ action: 'info' }, 30000);
      if (!alive.current || expected !== operation.current) return;
      setInfo(catalog);
      setReady(true);
      if (saved !== null) {
        try {
          const record = parseGuestRecord(saved);
          const value = await runtime.request<GuestSnapshot>({ action: 'restore', record });
          if (alive.current && expected === operation.current) publish(value);
        } catch {
          if (alive.current && expected === operation.current) {
            setError(
              'The saved game is invalid or uses an unsupported version. Clear it or confirm a replacement.',
            );
            setReady(runtime.ready && current.current === null);
          }
        }
      } else if (fromDevice) {
        current.current = null;
        setGame(null);
      }
    } catch {
      if (alive.current && expected === operation.current)
        setError('Guest engine unavailable. Retry loading it.');
    } finally {
      if (alive.current && expected === operation.current) {
        lock.current = false;
        setBusy(false);
      }
    }
  };
  useEffect(() => {
    alive.current = true;
    void boot();
    return () => {
      alive.current = false;
      operation.current++;
      lock.current = false;
      engine.current?.dispose();
    };
  }, []);
  const persist = async (value: GuestSnapshot) => {
    if (revision.current === undefined) {
      if (alive.current)
        setWarning(
          'Device storage is unavailable. Play can continue in memory; changes may be lost on reload.',
        );
      return;
    }
    try {
      revision.current = await writeGuestSlot(revision.current, value.record);
      if (alive.current) {
        setHasSlot(true);
        setWarning('');
      }
    } catch (e) {
      if (alive.current) {
        if (e instanceof GuestConflict) {
          setConflict(true);
          setWarning('Another tab changed the device game. Reload it before continuing.');
        } else
          setWarning(
            'This change could not be saved. Play can continue in memory; reload may restore an older game.',
          );
      }
    }
  };
  const run = async (request: Record<string, unknown>) => {
    if (lock.current || !alive.current || !ready || conflict) return;
    lock.current = true;
    const expected = operation.current;
    setBusy(true);
    setError('');
    setSelected(null);
    setConfirm(null);
    try {
      const value = await engine.current!.request<GuestSnapshot>(request);
      if (!alive.current || expected !== operation.current) return;
      publish(value);
      await persist(value);
    } catch (e) {
      if (alive.current && expected === operation.current) {
        setError((e as Error).message);
        setReady(engine.current?.ready ?? false);
      }
    } finally {
      if (alive.current && expected === operation.current) {
        lock.current = false;
        setBusy(false);
      }
    }
  };
  const clear = async () => {
    if (lock.current || !alive.current) return;
    lock.current = true;
    setBusy(true);
    setConfirm(null);
    setError('');
    try {
      revision.current = await clearGuestSlot();
      if (alive.current) {
        current.current = null;
        setGame(null);
        setHasSlot(false);
        setWarning('');
        setConflict(false);
        setSelected(null);
      }
    } catch {
      if (alive.current)
        setError('The device game could not be cleared. Retry when storage is available.');
    } finally {
      if (alive.current) {
        lock.current = false;
        setBusy(false);
      }
    }
  };
  const start = (event: FormEvent) => {
    event.preventDefault();
    if (busy || lock.current || !ready || conflict) return;
    if (hasSlot || game) setConfirm('replace');
    else void run({ action: 'new', ruleset: rule, human_side: side });
  };
  const historical = !!game && game.record.cursor !== game.record.moves.length;
  const humanTurn = !!game && game.position.turn === game.record.human_side;
  const mayMove =
    ready && !busy && !conflict && !historical && !!game && !game.live_winner && humanTurn;
  const select = (index: number) => {
    if (!game || !mayMove) return;
    if (index < 0) {
      setSelected(null);
      return;
    }
    const legal = game.legal_moves;
    if (selected !== null && legal.some((m) => m.source === selected && m.target === index)) {
      void run({ action: 'move', source: selected, target: index });
      return;
    }
    setSelected(legal.some((m) => m.source === index) && selected !== index ? index : null);
  };
  return (
    <>
      <header className="guest-header">
        <strong>HNEFATAFL</strong>
        <nav aria-label={t('Play surfaces')}>
          <span aria-current="page">{t('Guest play')}</span>
          <a href="/" onClick={surfaceLink}>
            {t('Account single-player')}
          </a>
          <a href="/online" onClick={surfaceLink}>
            {t('Online multiplayer')}
          </a>
        </nav>
        <LanguageSelect />
        <AppearanceSettings />
      </header>
      <main className="guest-page">
        <h1>{t('Guest play · this browser only')}</h1>
        <p>
          {t(
            'Local, noncompetitive single-player. Games stay on this device and are never uploaded or added to account results.',
          )}
        </p>
        <p role="status">
          {t(
            busy
              ? 'Loading or computing in the browser…'
              : ready
                ? 'Local engine ready.'
                : 'Local engine is not ready.',
          )}
        </p>
        {busy && (
          <button
            className="button secondary"
            onClick={() => {
              operation.current++;
              engine.current?.dispose();
              lock.current = false;
              setBusy(false);
              setReady(false);
              setError('Guest computation cancelled. Retry loading the engine.');
            }}
          >
            {t('Cancel computation')}
          </button>
        )}
        {!ready && !busy && (
          <button className="button primary" onClick={() => void boot(current.current === null)}>
            {t('Retry loading engine')}
          </button>
        )}
        {error && (
          <p role="alert" className="error-banner">
            {t(error)}
          </p>
        )}
        {warning && (
          <p role="status" className="guest-warning">
            {t(warning)}
          </p>
        )}
        {conflict && (
          <button className="button secondary" disabled={busy} onClick={() => setConfirm('reload')}>
            {t('Reload saved device game')}
          </button>
        )}
        <div className="guest-layout">
          <section>
            {game ? (
              <>
                <div className="turn-banner" aria-live="polite">
                  <div>
                    <strong>
                      {historical
                        ? t('Guest replay · read only')
                        : game.live_winner
                          ? game.live_winner === 'draw'
                            ? t('Draw')
                            : t('{side} win', { side: t(sides[game.live_winner]) })
                          : t('{side} to move', { side: t(sides[game.position.turn]) })}
                    </strong>
                    <span>
                      {historical
                        ? t('Move {move} of {total}', {
                            move: game.record.cursor,
                            total: game.record.moves.length,
                          })
                        : t(humanTurn ? 'Your turn' : 'Computer’s turn')}
                    </span>
                  </div>
                </div>
                <Board
                  game={{
                    ...game.position,
                    size: game.size,
                    moves: game.record.moves,
                    cursor: game.record.cursor,
                    legal_moves: historical ? [] : game.legal_moves,
                  }}
                  selected={selected}
                  onSelect={select}
                  readOnly={!mayMove}
                />
                {game.live_reason && <p>{t(game.live_reason.replaceAll('_', ' '))}</p>}
                {!historical && !game.live_winner && !humanTurn && (
                  <button
                    className="button primary"
                    disabled={!ready || busy || conflict}
                    onClick={() => void run({ action: 'computer' })}
                  >
                    {t('Play computer move')}
                  </button>
                )}
                <nav className="guest-actions" aria-label={t('Guest replay')}>
                  <button
                    className="button secondary"
                    disabled={busy || !ready || conflict || game.record.cursor === 0}
                    onClick={() => void run({ action: 'seek', cursor: game.record.cursor - 1 })}
                  >
                    {t('Previous move')}
                  </button>
                  <button
                    className="button secondary"
                    disabled={busy || !ready || conflict || !historical}
                    onClick={() => void run({ action: 'seek', cursor: game.record.cursor + 1 })}
                  >
                    {t('Next move')}
                  </button>
                  {historical && (
                    <button
                      className="button secondary"
                      disabled={busy || !ready || conflict}
                      onClick={() => void run({ action: 'seek', cursor: game.record.moves.length })}
                    >
                      {t('Return to current guest position')}
                    </button>
                  )}
                </nav>
              </>
            ) : (
              <p>{t('Start a guest game or restore the one saved in this browser.')}</p>
            )}
          </section>
          <aside className="panel guest-settings">
            <h2>{t('One guest game on this device')}</h2>
            <form onSubmit={start}>
              <fieldset disabled={busy || !ready || conflict || !!confirm}>
                <label>
                  {t('Ruleset')}
                  <select value={rule} onChange={(e) => setRule(e.target.value)}>
                    {info?.rulesets.map((r) => (
                      <option key={r.id} value={r.id}>
                        {t(r.name)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  {t('Your side')}
                  <select value={side} onChange={(e) => setSide(e.target.value as Side)}>
                    <option value="attackers">{t('Attackers')}</option>
                    <option value="defenders">{t('Defenders')}</option>
                  </select>
                </label>
                <p>{t('Computer: bounded baseline AI running in this browser.')}</p>
                <button className="button primary">{t('Start guest game')}</button>
              </fieldset>
            </form>
            <button
              className="button secondary"
              disabled={busy || (!hasSlot && !game)}
              onClick={() => setConfirm('clear')}
            >
              {t('Clear device game')}
            </button>
            <p>
              {t(
                'Only the current or last guest game is kept. Starting another replaces it after confirmation. There is no guest archive.',
              )}
            </p>
            <p>
              {t(
                'Browser storage can be cleared or evicted. Initial loading needs a connection and can take several seconds; full offline startup is not provided.',
              )}
            </p>
            {ready && (
              <p>
                <a href={engine.current?.notices} target="_blank" rel="noreferrer">
                  {t('Runtime licenses and source')}
                </a>
              </p>
            )}
            <p>
              {t(
                'Language and appearance are separate preferences. Logging in does not upload or delete this guest game.',
              )}
            </p>
          </aside>
        </div>
      </main>
      {confirm && (
        <Modal
          initialFocus="[data-guest-cancel]"
          title={t(
            confirm === 'replace'
              ? 'Replace device game?'
              : confirm === 'clear'
                ? 'Clear device game?'
                : 'Reload saved device game?',
          )}
          onClose={() => setConfirm(null)}
        >
          <p>
            {t(
              confirm === 'replace'
                ? 'This replaces the one saved guest game. It cannot be recovered from a guest archive.'
                : confirm === 'clear'
                  ? 'This removes the saved guest game from this browser. Appearance preferences and account games are kept.'
                  : 'Unsaved in-memory changes will be discarded. The saved device game will be validated again.',
            )}
          </p>
          <div className="guest-actions">
            <button data-guest-cancel className="button secondary" onClick={() => setConfirm(null)}>
              {t('Cancel')}
            </button>
            <button
              className="button primary"
              onClick={() =>
                confirm === 'clear'
                  ? void clear()
                  : confirm === 'replace'
                    ? void run({ action: 'new', ruleset: rule, human_side: side })
                    : (setConfirm(null), (current.current = null), setGame(null), void boot(true))
              }
            >
              {t(
                confirm === 'replace'
                  ? 'Confirm replacement'
                  : confirm === 'clear'
                    ? 'Confirm clear'
                    : 'Confirm reload',
              )}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
