import { useI18n } from './i18n';
import SupportDevelopmentLink from './SupportDevelopmentLink';
import './licenseNotice.css';

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
        <SupportDevelopmentLink />
      </span>
    </span>
  );
}
