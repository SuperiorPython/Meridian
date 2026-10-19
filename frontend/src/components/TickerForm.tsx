import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

const TICKER = /^[A-Z0-9.-]{1,10}$/;

export function TickerForm({ compact = false }: { compact?: boolean }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  function submit(e: FormEvent) {
    e.preventDefault();
    const ticker = value.trim().toUpperCase();
    if (!TICKER.test(ticker)) {
      setError("Enter a ticker symbol such as AAPL or BRK-B.");
      return;
    }
    setError(null);
    setValue("");
    navigate(`/report/${encodeURIComponent(ticker)}`);
  }

  return (
    <form onSubmit={submit} className={compact ? "ticker-form compact" : "ticker-form"} role="search">
      <label htmlFor={compact ? "ticker-compact" : "ticker"} className={compact ? "sr-only" : undefined}>
        Ticker symbol
      </label>
      <div className="ticker-row">
        <input
          id={compact ? "ticker-compact" : "ticker"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="AAPL"
          autoCapitalize="characters"
          autoComplete="off"
          spellCheck={false}
          aria-describedby={error ? "ticker-error" : undefined}
        />
        <button className="button">Open report</button>
      </div>
      {error && (
        <p id="ticker-error" role="alert" className="error">
          {error}
        </p>
      )}
    </form>
  );
}
