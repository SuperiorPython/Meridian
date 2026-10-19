import { Link, NavLink, useLocation } from "react-router-dom";
import { TickerForm } from "./TickerForm";

export function Header({ authEnabled, onSignOut }: { authEnabled: boolean; onSignOut: () => void }) {
  const { pathname } = useLocation();
  return (
    <header className="header">
      <Link to="/" className="brand">
        Meridian
      </Link>
      <nav className="nav" aria-label="Main">
        <NavLink to="/" end>
          Look up
        </NavLink>
        <NavLink to="/watchlist">Watchlist</NavLink>
        <NavLink to="/journal">Journal</NavLink>
      </nav>
      {pathname !== "/" && <TickerForm compact />}
      {authEnabled && (
        <button className="link-button" onClick={onSignOut}>
          Sign out
        </button>
      )}
    </header>
  );
}
