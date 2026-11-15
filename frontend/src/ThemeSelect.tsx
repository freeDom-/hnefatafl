import { useAppearance } from './Appearance';
import { UI_THEMES, resolveUiTheme, type UiTheme } from './appearanceConfig';
import { useI18n } from './i18n';

/** The shell's theme picker: the four supported named themes. */
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
      </select>
    </label>
  );
}
