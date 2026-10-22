import { useI18n } from './i18n';

export default function SearchWrapper({
  value,
  label,
  onChange,
}: {
  value: string;
  label: string;
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  return (
    <label>
      {t('Search wrapper')}
      <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="mcts">{t('MCTS (default)')}</option>
        <option value="alpha_beta">{t('Alpha-beta')}</option>
        <option value="none">{t('None (policy only)')}</option>
      </select>
    </label>
  );
}
