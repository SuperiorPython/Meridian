import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { dayLabel } from "../format";
import type { WatchlistItem } from "../types";

const TICKER = /^[A-Z0-9.-]{1,10}$/;

function Row({
  item,
  onChange,
  onRemove,
}: {
  item: WatchlistItem;
  onChange: (item: WatchlistItem) => void;
  onRemove: (ticker: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState(item.note);
  const [error, setError] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const updated = await api<WatchlistItem>(`/watchlist/${encodeURIComponent(item.ticker)}`, {
        method: "PUT",
        body: JSON.stringify({ note }),
      });
      onChange(updated);
      setEditing(false);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function remove() {
    setError(null);
    try {
      await api(`/watchlist/${encodeURIComponent(item.ticker)}`, { method: "DELETE" });
      onRemove(item.ticker);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <li className="row">
      <div className="row-main">
        <Link to={`/report/${item.ticker}`} className="ticker-link">
          {item.ticker}
        </Link>
        <span className="muted small">added {dayLabel(item.added_at)}</span>
      </div>
      {editing ? (
        <form onSubmit={save} className="inline-form">
          <label className="sr-only" htmlFor={`note-${item.ticker}`}>
            Note for {item.ticker}
          </label>
          <input id={`note-${item.ticker}`} value={note} onChange={(e) => setNote(e.target.value)} autoFocus />
          <button className="button">Save note</button>
          <button
            type="button"
            className="link-button"
            onClick={() => {
              setNote(item.note);
              setEditing(false);
            }}
          >
            Cancel
          </button>
        </form>
      ) : (
        <p className={item.note ? "row-note" : "row-note muted"}>{item.note || "No note"}</p>
      )}
      <div className="row-actions">
        {!editing && (
          <button className="link-button" onClick={() => setEditing(true)}>
            Edit note
          </button>
        )}
        <button className="link-button" onClick={remove}>
          Remove
        </button>
      </div>
      {error && (
        <p role="alert" className="error small">
          {error}
        </p>
      )}
    </li>
  );
}

export function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [ticker, setTicker] = useState("");
  const [note, setNote] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Watchlist · Meridian";
    const controller = new AbortController();
    api<{ items: WatchlistItem[] }>("/watchlist", { signal: controller.signal })
      .then((r) => setItems(r.items))
      .catch((err: Error) => {
        if (err.name !== "AbortError") setLoadError(err.message);
      });
    return () => {
      controller.abort();
      document.title = "Meridian";
    };
  }, []);

  async function add(e: FormEvent) {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!TICKER.test(t)) {
      setFormError("Enter a ticker symbol such as AAPL or BRK-B.");
      return;
    }
    setFormError(null);
    try {
      const item = await api<WatchlistItem>(`/watchlist/${encodeURIComponent(t)}`, {
        method: "PUT",
        body: JSON.stringify({ note }),
      });
      setItems((cur) =>
        [...(cur ?? []).filter((i) => i.ticker !== item.ticker), item].sort((a, b) =>
          a.ticker.localeCompare(b.ticker),
        ),
      );
      setTicker("");
      setNote("");
    } catch (err) {
      setFormError((err as Error).message);
    }
  }

  return (
    <section className="reading">
      <h1 className="title">Watchlist</h1>
      <p className="lede">Companies you're keeping an eye on, with a note on why.</p>

      <form onSubmit={add} className="add-form">
        <div className="field">
          <label htmlFor="wl-ticker">Ticker</label>
          <input
            id="wl-ticker"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="MSFT"
            autoComplete="off"
            spellCheck={false}
            className="upper"
          />
        </div>
        <div className="field grow">
          <label htmlFor="wl-note">Note (optional)</label>
          <input id="wl-note" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <button className="button">Add to watchlist</button>
      </form>
      {formError && (
        <p role="alert" className="error">
          {formError}
        </p>
      )}

      {loadError && (
        <p role="alert" className="error">
          {loadError}
        </p>
      )}
      {items === null && !loadError && <p className="muted">Loading…</p>}
      {items !== null && items.length === 0 && (
        <p className="muted empty">
          Nothing here yet. Add a ticker above, or open a company's report and choose Add to watchlist.
        </p>
      )}
      {items !== null && items.length > 0 && (
        <ul className="rows">
          {items.map((item) => (
            <Row
              key={item.ticker}
              item={item}
              onChange={(u) => setItems((cur) => (cur ?? []).map((i) => (i.ticker === u.ticker ? u : i)))}
              onRemove={(t) => setItems((cur) => (cur ?? []).filter((i) => i.ticker !== t))}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
