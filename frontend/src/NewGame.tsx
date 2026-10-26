import { useState, type FormEvent } from 'react';
import { api } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import Modal from './Modal';
import { sortRulesets } from './sortRulesets';
import type { AIProfile, Game, Ruleset, Side } from './types';

export const presetIds = ['baseline', 'shield_maiden', 'alpha_beta', 'mcts'] as const;
type Preset = (typeof presetIds)[number];
type Setup = {
  ruleset: string;
  mode: 'human-ai' | 'ai-ai';
  humanSide: Side;
  profiles: Record<Side, Preset>;
};
export const GAME_SETUP_KEY = 'hnefatafl-new-game-options';
const preset = (value: unknown): Preset =>
  presetIds.includes(value as Preset) ? (value as Preset) : 'baseline';
/** Read only the permitted subset of previous preferences; never restore identities/config. */
function restore(rulesets: Ruleset[], admin: boolean, initial?: string): Setup {
  let saved: Record<string, unknown> = {};
  try {
    const value = JSON.parse(localStorage.getItem(GAME_SETUP_KEY) ?? 'null');
    if (value && typeof value === 'object' && !Array.isArray(value)) saved = value;
  } catch {
    /* Browser preferences are optional. */
  }
  const exists = (id: unknown): id is string =>
    typeof id === 'string' && rulesets.some((rule) => rule.id === id);
  const old = saved.computers as Record<string, { profile_id?: string }> | undefined;
  const profiles = saved.profiles as Record<string, string> | undefined;
  return {
    ruleset: exists(initial)
      ? initial
      : exists(saved.ruleset)
        ? saved.ruleset
        : exists('tablut')
          ? 'tablut'
          : (rulesets[0]?.id ?? 'tablut'),
    mode: admin && saved.mode === 'ai-ai' ? 'ai-ai' : 'human-ai',
    humanSide: saved.humanSide === 'attackers' ? 'attackers' : 'defenders',
    profiles: {
      attackers: preset(profiles?.attackers ?? old?.attackers?.profile_id),
      defenders: preset(profiles?.defenders ?? old?.defenders?.profile_id),
    },
  };
}
const explanations: Record<Preset, string> = {
  baseline: 'A quick tactical opponent that weighs immediate captures and king safety.',
  shield_maiden: 'A tactical opponent focused on king escape routes and piece safety.',
  alpha_beta: 'Looks ahead through possible replies, pruning unpromising branches.',
  mcts: 'Explores promising continuations through simulated play.',
};
export default function NewGame({
  rulesets,
  profiles,
  initialRuleset,
  onClose,
  onCreated,
}: {
  rulesets: Ruleset[];
  profiles: AIProfile[];
  initialRuleset?: string;
  onClose: () => void;
  onCreated: (game: Game) => void;
}) {
  const { account } = useAuth();
  const { t } = useI18n();
  const admin = account?.permissions.includes('all_data') ?? false;
  const [setup, setSetup] = useState(() => restore(rulesets, admin, initialRuleset));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mode = admin ? setup.mode : 'human-ai';
  const choose = (key: 'ruleset' | 'mode' | 'humanSide', value: string) =>
    setSetup((previous) => ({ ...previous, [key]: value }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const players = Object.fromEntries(
        (['attackers', 'defenders'] as Side[])
          .filter((side) => mode === 'ai-ai' || side !== setup.humanSide)
          .map((side) => [side, { profile_id: setup.profiles[side] }]),
      );
      const game = await api<Game>('/games', {
        ruleset: setup.ruleset,
        mode,
        human_side: setup.humanSide,
        players,
      });
      try {
        localStorage.setItem(GAME_SETUP_KEY, JSON.stringify({ version: 2, ...setup, mode }));
      } catch {
        /* Creation remains usable without preferences. */
      }
      onCreated(game);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={t('Begin a new saga')} onClose={onClose}>
      <form className="new-game-form" onSubmit={(event) => void submit(event)}>
        <fieldset disabled={busy}>
          <legend>{t('01 · Choose your battlefield')}</legend>
          <div className="ruleset-options">
            {sortRulesets(rulesets).map((rule) => (
              <label key={rule.id} className={setup.ruleset === rule.id ? 'chosen' : ''}>
                <input
                  type="radio"
                  name="ruleset"
                  checked={setup.ruleset === rule.id}
                  onChange={() => choose('ruleset', rule.id)}
                />
                <strong>{t(rule.name)}</strong>
                <span>{t('{size} × {size} board', { size: rule.size })}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset disabled={busy}>
          <legend>{t('02 · Choose your opponent')}</legend>
          {admin && (
            <div className="segmented" aria-label={t('Game mode')}>
              {(['human-ai', 'ai-ai'] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={mode === value ? 'active' : ''}
                  onClick={() => choose('mode', value)}
                >
                  {t(value === 'human-ai' ? 'Human vs AI' : 'AI vs AI')}
                </button>
              ))}
            </div>
          )}
          {mode === 'human-ai' && (
            <>
              <div className="account-player-card">
                <strong>{account?.username}</strong>
                <span>{t('Playing as your account')}</span>
              </div>
              <label>
                {t('Your side')}
                <select
                  value={setup.humanSide}
                  onChange={(event) => choose('humanSide', event.target.value)}
                >
                  <option value="defenders">{t('Defenders · help the king escape')}</option>
                  <option value="attackers">{t('Attackers · capture the king')}</option>
                </select>
              </label>
            </>
          )}
          <div className="player-configs">
            {(['attackers', 'defenders'] as Side[])
              .filter((side) => mode === 'ai-ai' || side !== setup.humanSide)
              .map((side) => {
                const available = profiles.filter(
                  (profile) =>
                    presetIds.includes(profile.id as Preset) &&
                    profile.supported_rulesets.includes(setup.ruleset),
                );
                const selected = available.find((profile) => profile.id === setup.profiles[side]);
                return (
                  <div className="player-config" key={side}>
                    <label>
                      {t('{side} opponent', {
                        side: t(side === 'attackers' ? 'Attackers' : 'Defenders'),
                      })}
                      <select
                        aria-label={t('{side} AI profile', {
                          side: t(side === 'attackers' ? 'Attackers' : 'Defenders'),
                        })}
                        value={setup.profiles[side]}
                        required
                        onChange={(event) =>
                          setSetup((previous) => ({
                            ...previous,
                            profiles: { ...previous.profiles, [side]: preset(event.target.value) },
                          }))
                        }
                      >
                        {available.map((profile) => (
                          <option key={profile.id} value={profile.id}>
                            {t(profile.name)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <p>{t(explanations[setup.profiles[side]])}</p>
                    {selected && (
                      <p className="subtle">
                        {t('Time limit (seconds)')}: {selected.parameters.time_limit}
                        {selected.id === 'alpha_beta'
                          ? ` · ${t('Search depth')}: ${selected.parameters.depth}`
                          : selected.id === 'mcts'
                            ? ` · ${t('Search iterations')}: ${selected.parameters.iterations}`
                            : ''}
                      </p>
                    )}
                  </div>
                );
              })}
          </div>
        </fieldset>
        {error && (
          <p className="form-error" role="alert">
            {t(error)}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            {t('Cancel')}
          </button>
          <button
            className="button primary"
            disabled={busy || !rulesets.length || !profiles.length}
          >
            {t(busy ? 'Preparing…' : 'To the table')}
          </button>
        </div>
      </form>
    </Modal>
  );
}
