import type { ReactNode } from 'react';
import { useAuth } from './Auth';
import { useI18n } from './i18n';

export default function GameCreationActions({
  onNewOnline,
  onNewSingle,
  children,
}: {
  onNewOnline: () => void;
  onNewSingle?: () => void;
  children?: ReactNode;
}) {
  const { account } = useAuth();
  const { t } = useI18n();
  return (
    <div className="dashboard-actions">
      <button
        className="button primary"
        onClick={onNewOnline}
        disabled={account?.email_verified === false}
      >
        {t('New online game')}
      </button>
      <button className="button secondary" onClick={onNewSingle}>
        {t('New single-player game')}
      </button>
      {children}
    </div>
  );
}
