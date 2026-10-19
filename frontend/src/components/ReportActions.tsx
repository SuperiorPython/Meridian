import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { WatchlistItem } from "../types";

export function ReportActions({ ticker, close }: { ticker: string; close: number | null }) {
  const [onList, setOnList] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const path = `/watchlist/${encodeURIComponent(ticker)}`;

  useEffect(() => {
    const controller = new AbortController();
    setOnList(null);
    api<{ items: WatchlistItem[] }>("/watchlist", { signal: controller.signal })
      .then((r) => setOnList(r.items.some((i) => i.ticker === ticker)))
      .catch((err: Error) => {
        if (err.name !== "AbortError") setError(err.message);
      });
    return () => controller.abort();
  }, [ticker]);

  async function toggle() {
    setError(null);
    try {
      if (onList) await api(path, { method: "DELETE" });
      else await api(path, { method: "PUT", body: JSON.stringify({ note: "" }) });
      setOnList(!onList);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const journalLink = `/journal?ticker=${encodeURIComponent(ticker)}${close !== null ? `&price=${close.toFixed(2)}` : ""}`;
  return (
    <div className="actions">
      <div className="row-actions">
        {onList !== null && (
          <button className="button secondary" onClick={toggle}>
            {onList ? "Remove from watchlist" : "Add to watchlist"}
          </button>
        )}
        <Link to={journalLink} className="button secondary">
          Log a decision
        </Link>
      </div>
      {error && (
        <p role="alert" className="error small">
          {error}
        </p>
      )}
    </div>
  );
}
