import { useI18n } from './i18n';
import { BUY_ME_A_COFFEE_URL } from './supportLinks';

export default function SupportDevelopmentLink() {
  const { t } = useI18n();
  return (
    <a
      className="support-development-link"
      href={BUY_ME_A_COFFEE_URL}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={t('Support development on Buy Me a Coffee (opens in a new tab)')}
    >
      {t('Support development')}
    </a>
  );
}
