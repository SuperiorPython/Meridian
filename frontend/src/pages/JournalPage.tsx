import { FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { JournalEntryCard } from "../components/JournalEntryCard";
import type { Decision, JournalEntry } from "../types";

const TICKER = /^[A-Z0-9.-]{1,10}$/;
const DECISIONS: [Decision, string][] = [
  ["buy", "Buy"],
  ["sell", "Sell"],
  ["hold", "Hold"],
  ["watch", "Watch"],
  ["pass", "Pass"],
];

export function JournalPage() {
  const [params, setParams] = useSearchParams();
  const [entries, setEntries] = useState<JournalEntry[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [ticker, setTicker] = useState((params.get("ticker") ?? "").toUpperCase());
  const [decision, setDecision] = useState<Decision>("watch");
  const [thesis, setThesis] = useState("");
  const [conviction, setConviction] = useState("");
  const [priceText, setPriceText] = useState(params.get("price") ?? "");
  const [reviewOn, setReviewOn] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    document.title = "Journal · Meridian";
    const controller = new AbortController();
    api<{ entries: JournalEntry[] }>("/journal", { signal: controller.signal })
      .then((r) => setEntries(r.entries))
      .catch((err: Error) => {
        if (err.name !== "AbortError") setLoadError(err.message);
      });
    return () => {
      controller.abort();
      document.title = "Meridian";
    };
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!TICKER.test(t)) return setFormError("Enter a ticker symbol such as AAPL or BRK-B.");
    if (!thesis.trim()) return setFormError("Write your reasoning. It can't be edited later.");
    const priceValue = priceText.trim() === "" ? null : Number(priceText);
    if (priceValue !== null && !(priceValue > 0)) return setFormError("Price must be a positive number.");

    setFormError(null);
    setSaving(true);
    try {
      const entry = await api<JournalEntry>("/journal", {
        method: "POST",
        body: JSON.stringify({
          ticker: t,
          decision,
          thesis,
          conviction: conviction ? Number(conviction) : null,
          price: priceValue,
          review_on: reviewOn || null,
        }),
      });
      setEntries((cur) => [entry, ...(cur ?? [])]);
      setThesis("");
      setConviction("");
      setReviewOn("");
      setPriceText("");
      setTicker("");
      setParams({}, { replace: true });
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const shown = useMemo(() => {
    const f = filter.trim().toUpperCase();
    return (entries ?? []).filter((e) => !f || e.ticker.includes(f));
  }, [entries, filter]);

  return (
    <section className="reading">
      <h1 className="title">Decision journal</h1>
      <p className="lede">
        Write down why you made a call while you still know it. Your reasoning is locked once saved, so
        a later review can't be rewritten with hindsight.
      </p>

      <form onSubmit={submit} className="journal-form">
        <div className="field-row">
          <div className="field">
            <label htmlFor="j-ticker">Ticker</label>
            <input
              id="j-ticker"
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              className="upper"
            />
          </div>
          <div className="field">
            <label htmlFor="j-decision">Decision</label>
            <select id="j-decision" value={decision} onChange={(e) => setDecision(e.target.value as Decision)}>
              {DECISIONS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="j-price">Price (optional)</label>
            <input
              id="j-price"
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              value={priceText}
              onChange={(e) => setPriceText(e.target.value)}
            />
          </div>
        </div>
        <div className="field">
          <label htmlFor="j-thesis">Your reasoning</label>
          <textarea id="j-thesis" value={thesis} onChange={(e) => setThesis(e.target.value)} rows={4} />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="j-conviction">Conviction (optional)</label>
            <select id="j-conviction" value={conviction} onChange={(e) => setConviction(e.target.value)}>
              <option value="">Not set</option>
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n} of 5
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="j-review">Review on (optional)</label>
            <input id="j-review" type="date" value={reviewOn} onChange={(e) => setReviewOn(e.target.value)} />
          </div>
        </div>
        {formError && (
          <p role="alert" className="error">
            {formError}
          </p>
        )}
        <div>
          <button className="button" disabled={saving}>
            {saving ? "Saving…" : "Save entry"}
          </button>
        </div>
      </form>

      <h2>Entries</h2>
      {entries !== null && entries.length > 1 && (
        <div className="field filter">
          <label htmlFor="j-filter">Filter by ticker</label>
          <input id="j-filter" value={filter} onChange={(e) => setFilter(e.target.value)} className="upper" />
        </div>
      )}
      {loadError && (
        <p role="alert" className="error">
          {loadError}
        </p>
      )}
      {entries === null && !loadError && <p className="muted">Loading…</p>}
      {entries !== null && entries.length === 0 && (
        <p className="muted empty">No entries yet. Your first decision goes in the form above.</p>
      )}
      {entries !== null && entries.length > 0 && shown.length === 0 && (
        <p className="muted empty">No entries match that ticker.</p>
      )}
      {shown.length > 0 && (
        <ul className="entries">
          {shown.map((entry) => (
            <JournalEntryCard
              key={entry.id}
              entry={entry}
              onChange={(u) => setEntries((cur) => (cur ?? []).map((e) => (e.id === u.id ? u : e)))}
              onDelete={(id) => setEntries((cur) => (cur ?? []).filter((e) => e.id !== id))}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
