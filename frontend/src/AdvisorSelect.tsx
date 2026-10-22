import type { Advisor, AnalyticsSettings } from './analysisTypes';
import type { AIProfile, Game, Model } from './types';
import { selectStrongestModel } from './modelSelection';
import { useI18n } from './i18n';
import { modelLabel } from './modelLabel';
import { profileConfiguration } from './gameSetupPreference';

export function advisorOptions(profiles: AIProfile[], models: Model[], ruleset: string) {
  const options: { id: string; label: string; rune?: string; advisor: Advisor }[] = profiles
    .filter(
      (profile) => profile.algorithm !== 'neural' && profile.supported_rulesets.includes(ruleset),
    )
    .sort(
      (a, b) =>
        ['baseline', 'mcts', 'alpha_beta'].indexOf(a.algorithm) -
        ['baseline', 'mcts', 'alpha_beta'].indexOf(b.algorithm),
    )
    .map((profile) => ({
      id: `profile:${profile.id}`,
      label: profile.name,
      rune: profile.rune,
      advisor: {
        name: profile.name,
        profile_id: profile.id,
        config: profileConfiguration(profile),
      },
    }));
  const neural = profiles.find((profile) => profile.algorithm === 'neural');
  for (const model of models.filter((model) => model.ruleset === ruleset)) {
    options.push({
      id: `model:${model.id}`,
      label: modelLabel(model),
      advisor: {
        name: model.name,
        profile_id: neural?.id ?? 'neural',
        config: { ...neural?.parameters, algorithm: 'neural', model_id: model.id },
      },
    });
  }
  return options;
}

export default function AdvisorSelect({
  profiles,
  models,
  ruleset,
  value,
  onChange,
}: {
  profiles: AIProfile[];
  models: Model[];
  ruleset: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  return (
    <fieldset className="advisor-settings">
      <legend>{t('Move advisor')}</legend>
      <label>
        {t('Agent for move suggestions')}
        <select value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">{t('None')}</option>
          {advisorOptions(profiles, models, ruleset).map((option) => (
            <option value={option.id} key={option.id}>
              {option.id.startsWith('profile:')
                ? `${option.rune ? `${option.rune} ` : ''}${t(option.label)}`
                : option.label}
            </option>
          ))}
        </select>
      </label>
      <p className="subtle">
        {t(
          'Highlights this agent’s best legal move on the board. The advisor does not play your moves.',
        )}
      </p>
    </fieldset>
  );
}

export function AnalyticsSettingsPanel({
  game,
  profiles,
  models,
  settings,
  onChange,
  pending = false,
  error,
}: {
  game: Game;
  profiles: AIProfile[];
  models: Model[];
  settings: AnalyticsSettings;
  onChange: (next: AnalyticsSettings) => Promise<void>;
  pending?: boolean;
  error?: string;
}) {
  const { t } = useI18n();
  const options = advisorOptions(profiles, models, game.ruleset);
  const advisor = settings.advisor;
  const value = !advisor
    ? ''
    : !advisor.profile_id
      ? 'custom'
      : advisor.config.model_id
        ? `model:${advisor.config.model_id}`
        : `profile:${advisor.profile_id}`;
  const custom = value === 'custom';
  const compatibleModels = models.filter((model) => model.ruleset === game.ruleset);
  const update = (next: Partial<AnalyticsSettings>) => void onChange({ ...settings, ...next });
  const updateConfig = (key: string, input: string | number) => {
    if (!advisor) return;
    const config = { ...advisor.config, [key]: input };
    if (key === 'algorithm') {
      if (input === 'neural' && !compatibleModels.length) return;
      delete config.model_id;
      if (input === 'neural')
        config.model_id = selectStrongestModel(models, game.ruleset)?.model.id ?? '';
    }
    update({ advisor: { ...advisor, config } });
  };
  const choose = (selection: string) => {
    if (!selection) update({ advisor: null });
    else if (selection === 'custom')
      update({
        advisor: {
          name: 'Custom move advisor',
          config: {
            algorithm: 'alpha_beta',
            depth: 5,
            iterations: 5000,
            temperature: 0,
            exploration: 1.4,
          },
        },
      });
    else update({ advisor: options.find((option) => option.id === selection)?.advisor ?? null });
  };
  return (
    <section className="panel analytics-settings" aria-label={t('Analytics')}>
      <h2>{t('Analytics')}</h2>
      <label>
        {t('Move advisor')}
        <select value={value} onChange={(event) => choose(event.target.value)}>
          <option value="">{t('None')}</option>
          <option value="custom">{t('Custom move advisor')}</option>
          {value && value !== 'custom' && !options.some((option) => option.id === value) && (
            <option value={value}>{advisor?.model_name ?? advisor?.name}</option>
          )}
          {options.map((option) => (
            <option value={option.id} key={option.id}>
              {option.id.startsWith('profile:')
                ? `${option.rune ? `${option.rune} ` : ''}${t(option.label)}`
                : option.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t('Maximum search time (seconds)')}
        <input
          type="number"
          min="0.05"
          max="15"
          step="0.05"
          value={settings.time_limit}
          onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isFinite(next) && next >= 0.05 && next <= 15) update({ time_limit: next });
          }}
        />
      </label>
      {custom && advisor && (
        <fieldset className="advisor-custom-fields">
          <legend>{t('Custom move advisor')}</legend>
          <label>
            {t('Algorithm')}
            <select
              value={String(advisor.config.algorithm ?? 'baseline')}
              onChange={(event) => updateConfig('algorithm', event.target.value)}
            >
              <option value="baseline">{t('Tactical heuristic')}</option>
              <option value="alpha_beta">{t('Alpha-beta search')}</option>
              <option value="mcts">{t('Monte Carlo tree search')}</option>
              <option value="neural" disabled={!compatibleModels.length}>
                {t('Neural network')}
              </option>
            </select>
          </label>
          {advisor.config.algorithm === 'alpha_beta' && (
            <label>
              {t('Search depth')}
              <input
                type="number"
                min="1"
                max="5"
                value={Number(advisor.config.depth ?? 5)}
                onChange={(event) => updateConfig('depth', Number(event.target.value))}
              />
            </label>
          )}
          {['mcts', 'neural'].includes(String(advisor.config.algorithm)) && (
            <>
              <label>
                {t('Search iterations')}
                <input
                  type="number"
                  min="1"
                  max="5000"
                  value={Number(advisor.config.iterations ?? 5000)}
                  onChange={(event) => updateConfig('iterations', Number(event.target.value))}
                />
              </label>
              <label>
                {t('Exploration')}
                <input
                  type="number"
                  min="0"
                  max="10"
                  step="0.1"
                  value={Number(advisor.config.exploration ?? 1.4)}
                  onChange={(event) => updateConfig('exploration', Number(event.target.value))}
                />
              </label>
              <label>
                {t('Temperature')}
                <input
                  type="number"
                  min="0"
                  max="2"
                  step="0.1"
                  value={Number(advisor.config.temperature ?? 0)}
                  onChange={(event) => updateConfig('temperature', Number(event.target.value))}
                />
              </label>
            </>
          )}
          {advisor.config.algorithm === 'neural' && (
            <label>
              {t('Trained model')}
              <select
                value={String(advisor.config.model_id ?? '')}
                onChange={(event) => updateConfig('model_id', event.target.value)}
              >
                <option value="">{t('Choose a compatible model')}</option>
                {models
                  .filter((model) => model.ruleset === game.ruleset)
                  .map((model) => (
                    <option key={model.id} value={model.id}>
                      {modelLabel(model)}
                    </option>
                  ))}
              </select>
              {!models.some((model) => model.ruleset === game.ruleset) && (
                <small>{t('Train or import a model in the AI workshop first.')}</small>
              )}
            </label>
          )}
        </fieldset>
      )}
      <p className="subtle">
        {t('Analysis runs in the background. You can keep playing while the advisor searches.')}
      </p>
      {pending && (
        <p className="subtle" role="status">
          {t('Saving analytics settings…')}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
