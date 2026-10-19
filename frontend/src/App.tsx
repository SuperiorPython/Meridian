import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError, getKey, setKey, UNAUTHORIZED_EVENT } from "./api";
import { Header } from "./components/Header";
import { Login } from "./components/Login";
import { Home } from "./pages/Home";
import { JournalPage } from "./pages/JournalPage";
import { ReportPage } from "./pages/ReportPage";
import { WatchlistPage } from "./pages/WatchlistPage";
import type { AuthStatus } from "./types";

type Session =
  | { state: "checking" }
  | { state: "login" }
  | { state: "error"; message: string }
  | { state: "ready"; authEnabled: boolean };

export default function App() {
  const [session, setSession] = useState<Session>({ state: "checking" });

  const check = useCallback(async () => {
    setSession({ state: "checking" });
    try {
      const status = await api<AuthStatus>("/auth/check");
      setSession({ state: "ready", authEnabled: status.auth_enabled });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setKey(null);
        setSession({ state: "login" });
      } else {
        setSession({ state: "error", message: (err as Error).message });
      }
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  // Any request that comes back 401 (e.g. the key was changed) sends you to the login screen.
  useEffect(() => {
    const onUnauthorized = () => {
      setKey(null);
      setSession({ state: "login" });
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  const signOut = () => {
    setKey(null);
    setSession({ state: "login" });
  };

  if (session.state === "checking") {
    return (
      <main className="centered" aria-live="polite">
        <p className="muted">Connecting to Meridian…</p>
      </main>
    );
  }

  if (session.state === "error") {
    return (
      <main className="centered">
        <h1 className="title">Can't connect</h1>
        <p>{session.message}</p>
        <button className="button" onClick={() => void check()}>
          Try again
        </button>
      </main>
    );
  }

  if (session.state === "login") {
    return <Login onSuccess={(authEnabled) => setSession({ state: "ready", authEnabled })} />;
  }

  return (
    <>
      <Header authEnabled={session.authEnabled && getKey() !== null} onSignOut={signOut} />
      <main className="page">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/report/:ticker" element={<ReportPage />} />
          <Route path="/watchlist" element={<WatchlistPage />} />
          <Route path="/journal" element={<JournalPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
