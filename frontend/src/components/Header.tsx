import { Link, useLocation } from "react-router-dom";
import { TickerForm } from "./TickerForm";

export function Header({ authEnabled, onSignOut }: { authEnabled: boolean; onSignOut: () => void }) {
  const { pathname } = useLocation();
  return (
    <header className="header">
      <Link to="/" className="brand">
        Meridian
      </Link>
      {pathname !== "/" && <TickerForm compact />}
      {authEnabled && (
        <button className="link-button" onClick={onSignOut}>
          Sign out
        </button>
      )}
    </header>
  );
}
