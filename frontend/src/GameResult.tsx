import { useI18n } from './i18n';
import type { Side } from './types';

export default function GameResult({
  winner,
  reason,
  labels,
}: {
  winner: Side | 'draw' | null;
  reason: string | null;
  labels: Record<Side, string>;
}) {
  const { t } = useI18n();
  return (
    <div className="game-result" role="status">
      <strong>
        {winner === 'draw'
          ? t('Draw')
          : winner
            ? t('{side} win', { side: t(labels[winner]) })
            : t('No winner')}
      </strong>
      {reason ? <> · {t(reason.replaceAll('_', ' '))}</> : null}
    </div>
  );
}
