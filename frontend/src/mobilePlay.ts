import { createContext, useCallback, useContext, useSyncExternalStore } from 'react';

export const MobilePlayContext = createContext(false);
export const useCompactPlay = () => useContext(MobilePlayContext);

export function useMediaQuery(query: string) {
  const subscribe = useCallback(
    (changed: () => void) => {
      const media = window.matchMedia?.(query);
      if (!media) return () => {};
      media.addEventListener('change', changed);
      return () => media.removeEventListener('change', changed);
    },
    [query],
  );
  const snapshot = useCallback(() => window.matchMedia?.(query).matches ?? false, [query]);
  return useSyncExternalStore(subscribe, snapshot, () => false);
}
