import { FormEvent, useState } from "react";
import { api, ApiError, setKey } from "../api";
import type { AuthStatus } from "../types";

export function Login({ onSuccess }: { onSuccess: (authEnabled: boolean) => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const candidate = value.trim();
    if (!candidate) return;
    setBusy(true);
    setError(null);
    try {
      const status = await api<AuthStatus>("/auth/check", { key: candidate });
      setKey(candidate);
      onSuccess(status.auth_enabled);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "That key doesn't match MERIDIAN_API_TOKEN on the server."
          : (err as Error).message,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="centered">
      <h1 className="brand-large">Meridian</h1>
      <form onSubmit={submit} className="stack">
        <label htmlFor="key">Access key</label>
        <input
          id="key"
          type="password"
          autoComplete="current-password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
        />
        <p className="muted small">The value of MERIDIAN_API_TOKEN in your server's .env file.</p>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <button className="button" disabled={busy || !value.trim()}>
          {busy ? "Checking…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
