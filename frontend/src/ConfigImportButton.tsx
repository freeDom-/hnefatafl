import { t } from './i18n';

/** Shared file picker for importing an exported training config.json. */
export default function ConfigImportButton({
  onImport,
  busy = false,
  disabled = false,
  label = 'Import config.json',
}: {
  onImport: (file: File) => void;
  busy?: boolean;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <label className={`button secondary small file-button ${busy || disabled ? 'disabled' : ''}`}>
      {busy ? t('Importing configuration…') : t(label)}
      <input
        type="file"
        accept=".json,application/json"
        aria-label={t(label)}
        disabled={busy || disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) onImport(file);
        }}
      />
    </label>
  );
}
