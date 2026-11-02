import { useEffect, useRef, useState, type FormEvent } from 'react';
import AgentConfigFields from './AgentConfigFields';
import { api, errorMessage } from './api';
import { useAuth } from './Auth';
import { useI18n } from './i18n';
import Modal from './Modal';
import { notify } from './notifications';
import { profileConfiguration } from './profileConfiguration';
import { sortRulesets } from './sortRulesets';
import type { TournamentPlayer } from './tournamentTypes';
import type { AIProfile, Game, Model, Ruleset, Side } from './types';

export const presetIds = ['baseline', 'shield_maiden', 'alpha_beta', 'mcts'] as const;
type Preset = (typeof presetIds)[number];
/** The administrator-only fully configured opponent. */
type Choice = Preset | 'custom';
type Setup = {
  ruleset: string;
  mode: 'human-ai' | 'ai-ai';
  humanSide: Side;
  profiles: Record<Side, Choice>;
  /** Working configuration for each seat while "Custom opponent" is selected. */
  custom: Record<Side, TournamentPlayer>;
};
export const GAME_SETUP_KEY = 'hnefatafl-new-game-options';
const preset = (value: unknown): Preset =>
  presetIds.includes(value as Preset) ? (value as Preset) : 'baseline';
/** A custom seat starts from the matching preset's budget until it is edited. */
const customPlayer = (side: Side): TournamentPlayer => ({
  name: 'Computer',
  config:
    side === 'attackers'
      ? { algorithm: 'baseline', time_limit: 1 }
      : { algorithm: 'alpha_beta', depth: 5, time_limit: 2 },
});
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
    custom: { attackers: customPlayer('attackers'), defenders: customPlayer('defenders') },
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
  models = [],
  initialRuleset,
  onClose,
  onCreated,
}: {
  rulesets: Ruleset[];
  profiles: AIProfile[];
  /** Registered models, if the caller already loaded them. */
  models?: Model[];
  initialRuleset?: string;
  onClose: () => void;
  onCreated: (game: Game) => void;
}) {
  const { account } = useAuth();
  const { t } = useI18n();
  const admin = account?.permissions.includes('all_data') ?? false;
  const [setup, setSetup] = useState(() => restore(rulesets, admin, initialRuleset));
  const [busy, setBusy] = useState(false);
  const [catalogue, setCatalogue] = useState<Model[] | null>(null);
  const [cataloguePending, setCataloguePending] = useState(false);
  const [catalogueError, setCatalogueError] = useState<string | null>(null);
  const [catalogueAttempt, setCatalogueAttempt] = useState(0);
  const seeded = useRef<Record<Side, boolean>>({ attackers: false, defenders: false });
  const mode = admin ? setup.mode : 'human-ai';
  // Custom opponents are a single-player, administrator-only resource.
  const customAvailable = admin && mode === 'human-ai';
  const customOpen =
    customAvailable &&
    (['attackers', 'defenders'] as Side[]).some((side) => setup.profiles[side] === 'custom');
  const availableModels = catalogue ?? models;
  // A catalogue the dashboard already loaded is usable; an empty list may simply
  // mean it was never fetched, so a custom seat still loads it on demand.
  const catalogueReady = catalogue !== null || models.length > 0;
  useEffect(() => {
    if (!customOpen || catalogueReady) return;
    let cancelled = false;
    const controller = new AbortController();
    setCataloguePending(true);
    setCatalogueError(null);
    // Models are large; load the catalogue only when a custom seat needs it.
    api<Model[]>('/models', undefined, undefined, controller.signal)
      .then((saved) => {
        if (cancelled) return;
        if (Array.isArray(saved)) setCatalogue(saved);
        else setCatalogueError(t('Trained models could not be loaded.'));
      })
      .catch((failure: unknown) => {
        // A failed request never replaces a catalogue that is already usable.
        if (cancelled) return;
        setCatalogueError(errorMessage(failure, t('Trained models could not be loaded.')));
      })
      .finally(() => {
        if (!cancelled) setCataloguePending(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
      setCataloguePending(false);
    };
  }, [customOpen, catalogueReady, catalogueAttempt, t]);
  const aiSides = (['attackers', 'defenders'] as Side[]).filter(
    (side) => mode === 'ai-ai' || side !== setup.humanSide,
  );
  const incompatibleModel = aiSides.some((side) => {
    if (setup.profiles[side] !== 'custom') return false;
    const { config } = setup.custom[side];
    if (config.algorithm !== 'neural' || !config.model_id) return false;
    return !availableModels.some(
      (model) => model.id === config.model_id && model.ruleset === setup.ruleset,
    );
  });
  const choose = (key: 'ruleset' | 'mode' | 'humanSide', value: string) =>
    setSetup((previous) => {
      const next: Setup = { ...previous, [key]: value };
      if (key === 'mode' && value !== 'human-ai')
        next.profiles = {
          attackers: preset(previous.profiles.attackers),
          defenders: preset(previous.profiles.defenders),
        };
      return next;
    });
  const chooseOpponent = (side: Side, value: string) => {
    if (value !== 'custom') {
      setSetup((previous) => ({
        ...previous,
        profiles: { ...previous.profiles, [side]: preset(value) },
      }));
      return;
    }
    // Seed an untouched custom seat from the opponent that was selected before.
    const seed = seeded.current[side]
      ? undefined
      : profiles.find((item) => item.id === setup.profiles[side]);
    seeded.current[side] = true;
    setSetup((previous) => ({
      ...previous,
      profiles: { ...previous.profiles, [side]: 'custom' },
      custom: seed
        ? { ...previous.custom, [side]: { name: 'Computer', config: profileConfiguration(seed) } }
        : previous.custom,
    }));
  };
  const editCustom = (side: Side, player: TournamentPlayer) =>
    setSetup((previous) => ({
      ...previous,
      custom: { ...previous.custom, [side]: player },
    }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const players = Object.fromEntries(
        aiSides.map((side) =>
          setup.profiles[side] === 'custom'
            ? [side, { config: setup.custom[side].config }]
            : [side, { profile_id: setup.profiles[side] }],
        ),
      );
      const game = await api<Game>('/games', {
        ruleset: setup.ruleset,
        mode,
        human_side: setup.humanSide,
        players,
      });
      try {
        // Preferences never retain a custom identity or configuration.
        localStorage.setItem(
          GAME_SETUP_KEY,
          JSON.stringify({
            version: 2,
            ruleset: setup.ruleset,
            humanSide: setup.humanSide,
            // A custom seat is stored as the preset it replaces: the
            // configuration itself is never kept as a browser preference.
            profiles: {
              attackers: preset(setup.profiles.attackers),
              defenders: preset(setup.profiles.defenders),
            },
            mode,
          }),
        );
      } catch {
        /* Creation remains usable without preferences. */
      }
      onCreated(game);
    } catch (failure) {
      notify.error(errorMessage(failure));
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
          )}
          <div className="player-configs">
            {aiSides.map((side) => {
              const available = profiles.filter(
                (profile) =>
                  presetIds.includes(profile.id as Preset) &&
                  profile.supported_rulesets.includes(setup.ruleset),
              );
              const selected = available.find((profile) => profile.id === setup.profiles[side]);
              const custom = setup.profiles[side] === 'custom';
              const label = t(side === 'attackers' ? 'Attackers' : 'Defenders');
              return (
                <div className="player-config" key={side}>
                  <label>
                    {t('{side} opponent', { side: label })}
                    <select
                      aria-label={t('{side} AI profile', { side: label })}
                      value={setup.profiles[side]}
                      required
                      onChange={(event) => chooseOpponent(side, event.target.value)}
                    >
                      {available.map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {t(profile.name)}
                        </option>
                      ))}
                      {customAvailable && <option value="custom">{t('Custom opponent')}</option>}
                    </select>
                  </label>
                  {custom ? (
                    <>
                      {catalogueError && !catalogueReady && (
                        <p className="form-error" role="alert">
                          {catalogueError}{' '}
                          <button
                            type="button"
                            className="button secondary small"
                            onClick={() => setCatalogueAttempt((value) => value + 1)}
                          >
                            {t('Retry')}
                          </button>
                        </p>
                      )}
                      <AgentConfigFields
                        player={setup.custom[side]}
                        models={availableModels}
                        ruleset={setup.ruleset}
                        prefix={label}
                        modelsPending={cataloguePending}
                        onChange={(next) => editCustom(side, next)}
                      />
                    </>
                  ) : (
                    <>
                      <p>{t(explanations[setup.profiles[side] as Preset])}</p>
                      {selected && (
                        <p className="subtle">
                          {t('Time limit (seconds)')}: {String(selected.parameters.time_limit)}
                          {selected.id === 'alpha_beta'
                            ? ` · ${t('Search depth')}: ${selected.parameters.depth}`
                            : selected.id === 'mcts'
                              ? ` · ${t('Search iterations')}: ${selected.parameters.iterations}`
                              : ''}
                        </p>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </fieldset>
        {incompatibleModel && (
          <p className="form-error">
            {t('Choose a compatible trained model and its single matching battlefield.')}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            {t('Cancel')}
          </button>
          <button
            className="button primary"
            disabled={busy || !rulesets.length || !profiles.length || incompatibleModel}
          >
            {t(busy ? 'Preparing…' : 'To the table')}
          </button>
        </div>
      </form>
    </Modal>
  );
}
