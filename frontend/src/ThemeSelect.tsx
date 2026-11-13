import { useAppearance } from './Appearance';
import {
  LEGACY_UI_THEME,
  LEGACY_UI_THEME_OPTION,
  UI_THEMES,
  resolveUiTheme,
  type UiTheme,
} from './appearanceConfig';
import { useI18n } from './i18n';

/** The shell's theme picker: the three named themes plus the legacy color-mode look. */
export default function ThemeSelect() {
  const { t } = useI18n();
  const { appearance, updateAppearance } = useAppearance();
  return (
    <label className="theme-select">
      <span className="theme-select-label">{t('THEME')}</span>
      <select
        aria-label={t('Theme')}
        value={resolveUiTheme(appearance)}
        onChange={(event) => updateAppearance({ uiTheme: event.target.value as UiTheme })}
      >
        {UI_THEMES.map((theme) => (
          <option key={theme.id} value={theme.id}>
            {t(theme.name)}
          </option>
        ))}
        <option value={LEGACY_UI_THEME}>{t(LEGACY_UI_THEME_OPTION.name)}</option>
      </select>
    </label>
  );
}
