import type { Advisor } from './analysisTypes';
import type { AIProfile, Model } from './types';
import { useI18n } from './i18n';
import { modelLabel } from './modelLabel';

export function advisorOptions(profiles: AIProfile[], models: Model[], ruleset: string) {
  const options: { id: string; label: string; rune?: string; advisor: Advisor }[] = profiles
    .filter(
      (profile) => profile.algorithm !== 'neural' && profile.supported_rulesets.includes(ruleset),
    )
    .map((profile) => ({
      id: `profile:${profile.id}`,
      label: profile.name,
      rune: profile.rune,
      advisor: {
        name: profile.name,
        profile_id: profile.id,
        config: { ...profile.parameters, algorithm: profile.algorithm },
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
