import { createContext, useContext, type ReactNode } from 'react';
import { useAchievementNotifications, type ProgressionValue } from './useAchievementNotifications';

const empty: ProgressionValue = {
  achievements: null,
  phase: 'loading',
  refresh: () => Promise.resolve(null),
};

const ProgressionContext = createContext<ProgressionValue>(empty);

/** The single shared achievement projection for the authenticated shell. */
export function useProgression(): ProgressionValue {
  return useContext(ProgressionContext);
}

/**
 * Owns the one account-scoped `/personal/achievements` read so the notification
 * hook, the Dashboard and the Portrait Hall never fetch it independently. A
 * signed-out tree receives an inert value, and a new account mounts a fresh
 * session keyed by owner, which aborts the previous account's in-flight read.
 */
export function ProgressionProvider({
  owner,
  intervalMs,
  children,
}: {
  owner: string | undefined;
  /** Recurring backstop for routes without their own completion signal; 0 disables it. */
  intervalMs?: number;
  children: ReactNode;
}) {
  if (!owner)
    return <ProgressionContext.Provider value={empty}>{children}</ProgressionContext.Provider>;
  return (
    <ProgressionSession key={owner} owner={owner} intervalMs={intervalMs}>
      {children}
    </ProgressionSession>
  );
}

function ProgressionSession({
  owner,
  intervalMs,
  children,
}: {
  owner: string;
  intervalMs: number | undefined;
  children: ReactNode;
}) {
  const value = useAchievementNotifications(owner, intervalMs);
  return <ProgressionContext.Provider value={value}>{children}</ProgressionContext.Provider>;
}
