import { useI18n } from './i18n';
import './licenseNotice.css';

const COMMERCIAL_LICENSING_URL = 'https://github.com/freeDom-/hnefatafl/issues';

export default function LicenseNotice() {
  const { t } = useI18n();
  return (
    <span className="license-notice">
      <span>{t('Source-available under the PolyForm Noncommercial License 1.0.0')}</span>
      <span className="license-notice-links">
        <a href="/LICENSE.txt" target="_blank" rel="noopener noreferrer">
          {t('License terms')}
        </a>
        <span aria-hidden="true">·</span>
        <a href="/THIRD_PARTY_NOTICES.txt" target="_blank" rel="noopener noreferrer">
          {t('Third-party notices')}
        </a>
        <span aria-hidden="true">·</span>
        <a href={COMMERCIAL_LICENSING_URL} target="_blank" rel="noopener noreferrer">
          {t('Commercial licensing')}
        </a>
      </span>
    </span>
  );
}
