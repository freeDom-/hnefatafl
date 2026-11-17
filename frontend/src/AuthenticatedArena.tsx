import Auth from './Auth';
import App from './App';

/** Protected application graph is never initialized by a public spectator route. */
export default function AuthenticatedArena() {
  return (
    <Auth>
      <App />
    </Auth>
  );
}
