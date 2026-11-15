import { useEffect, useState } from 'react';
import { RemainingTime, remainingSeconds } from './remainingTime';

/** Only this small leaf rerenders each second; deadlines remain server-owned. */
export default function DashboardCountdown({ deadline }: { deadline: string }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    const clear = () => clearInterval(timer);
    const refresh = () => {
      clear();
      const at = Date.now();
      setNow(at);
      if (!document.hidden && remainingSeconds(deadline, at) > 0) {
        timer = setInterval(() => {
          const at = Date.now();
          setNow(at);
          if (remainingSeconds(deadline, at) === 0) clear();
        }, 1000);
      }
    };
    refresh();
    document.addEventListener('visibilitychange', refresh);
    return () => {
      clear();
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [deadline]);
  return Number.isFinite(Date.parse(deadline)) ? (
    <RemainingTime seconds={remainingSeconds(deadline, now)} />
  ) : null;
}
