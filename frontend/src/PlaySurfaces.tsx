import { useEffect, useState } from 'react';
import Auth from './Auth';
import App from './App';
import Guest from './Guest';
import { isGuestPath } from './surfaceNavigation';
/** The guest surface never mounts authentication or account/game adapters. */
export default function PlaySurfaces() {
  const [guest, setGuest] = useState(() => isGuestPath(location.pathname));
  useEffect(() => {
    const changed = () => setGuest(isGuestPath(location.pathname));
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  return guest ? (
    <Guest />
  ) : (
    <Auth>
      <App />
    </Auth>
  );
}
