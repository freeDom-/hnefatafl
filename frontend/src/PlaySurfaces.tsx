import { useEffect, useState } from 'react';
import Auth from './Auth';
import App from './App';
import Guest from './Guest';
import GuestNavigation from './GuestNavigation';
import { isGuestPath } from './surfaceNavigation';
/** Guest gameplay stays public; only its header reads the current session. */
export default function PlaySurfaces() {
  const [guest, setGuest] = useState(() => isGuestPath(location.pathname));
  useEffect(() => {
    const changed = () => setGuest(isGuestPath(location.pathname));
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  return guest ? (
    <Guest header={<GuestNavigation />} />
  ) : (
    <Auth>
      <App />
    </Auth>
  );
}
