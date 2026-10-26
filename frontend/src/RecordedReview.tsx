import { useCallback, useState } from 'react';
import { api } from './api';
import Board from './Board';
import ReplayControls from './ReplayControls';
import { useOnlinePoll } from './useOnlinePoll';
import { useI18n } from './i18n';
import type { OnlineReplay } from './onlineTypes';
import type { AccountReviewData } from './personalTypes';
import { resultName } from './RecordedGames';

const ruleNames: Record<string, string> = {
  brandubh: 'Brandubh',
  tablut: 'Tablut',
  copenhagen: 'Copenhagen',
};
export default function RecordedReview({
  id,
  category,
  accountId,
  personal = false,
}: {
  id: string;
  category: 'online' | 'account-single-player';
  accountId: string;
  personal?: boolean;
}) {
  const { t } = useI18n();
  const url = personal
    ? `/personal/single-player/${id}/replay`
    : `/history/${category}/${id}/replay`;
  const load = useCallback(
    (signal: AbortSignal) =>
      api<OnlineReplay | AccountReviewData>(url, undefined, undefined, signal),
    [url],
  );
  const poll = useOnlinePoll(`review:${accountId}:${url}`, load);
  const [cursor, setCursor] = useState(0);
  const data = poll.data;
  const index = Math.min(cursor, Math.max(0, (data?.states.length ?? 1) - 1));
  const position = data?.states[index];
  const online = data?.game.category === 'online' ? (data as OnlineReplay) : null;
  const account =
    data?.game.category === 'account-single-player' ? (data as AccountReviewData) : null;
  return (
    <section className="recorded-review panel" aria-label={t('Selected game review')}>
      <h2>{t(category === 'online' ? 'Online game review' : 'Account game review')}</h2>
      <p>{t('Read-only review. Replay controls change only your view.')}</p>
      {poll.phase === 'loading' && <p role="status">{t('Loading your games…')}</p>}
      {poll.phase === 'stale' && (
        <p role="alert">{t('Review unavailable. Check your access or retry.')}</p>
      )}
      <button className="button secondary" onClick={() => void poll.refresh()}>
        {t(poll.phase === 'stale' ? 'Retry' : 'Refresh')}
      </button>
      {data?.validated && position && (
        <>
          <p>
            {t(ruleNames[data.game.ruleset] ?? data.game.ruleset)} · {data.game.id}
          </p>
          <p>
            {t('Lifecycle')}:{' '}
            {t(
              (
                {
                  completed: 'Completed',
                  active: 'Active',
                  waiting: 'Waiting',
                  cancelled: 'Cancelled',
                  voided: 'Voided',
                  in_progress: 'In progress',
                } as Record<string, string>
              )[data.game.status],
            )}
          </p>
          <p>
            {t('Recorded result')}:{' '}
            {t(
              online
                ? resultName(online.game.result?.winner ?? null)
                : resultName(account?.game.winner ?? null),
            )}
            {online?.game.result
              ? ` · ${t(online.game.result.termination_reason.replaceAll('_', ' '))}`
              : ''}
          </p>
          <div className="personal-review-board">
            <Board
              game={{
                ...position,
                size: Math.sqrt(position.board.length),
                moves: online ? online.game.moves : account!.moves,
                cursor: index,
                legal_moves: [],
              }}
              selected={null}
              onSelect={() => {}}
              readOnly
            />
          </div>
          <ReplayControls
            cursor={index}
            length={data.states.length}
            onChange={setCursor}
            disabled={poll.phase !== 'ready'}
          />
        </>
      )}
    </section>
  );
}
