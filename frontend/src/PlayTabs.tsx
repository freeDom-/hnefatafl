import { createContext, useContext, useState, type ReactNode } from 'react';

const PlayTabsContext = createContext<{ active: string; change: (tab: string) => void } | null>(
  null,
);

/** The selection belongs to this account's play surface, rather than one game. */
export function PlayTabsProvider({ children }: { children: ReactNode }) {
  const [active, change] = useState('moves');
  return <PlayTabsContext.Provider value={{ active, change }}>{children}</PlayTabsContext.Provider>;
}

export function usePlayTab(available: string[]) {
  const shared = useContext(PlayTabsContext);
  const [local, setLocal] = useState('moves');
  const preferred = shared?.active ?? local;
  return [available.includes(preferred) ? preferred : 'moves', shared?.change ?? setLocal] as const;
}
