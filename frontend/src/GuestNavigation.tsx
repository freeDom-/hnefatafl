import Auth, { useAuth } from './Auth';
import SiteHeader from './SiteHeader';

function SessionHeader() {
  const { account, logout, pending } = useAuth();
  return <SiteHeader account={account} logout={logout} pending={pending} />;
}

/** Session-aware navigation only; guest gameplay remains outside the auth guard. */
export default function GuestNavigation() {
  return (
    <Auth navigationOnly>
      <SessionHeader />
    </Auth>
  );
}
