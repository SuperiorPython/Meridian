import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { dateLabel, dayLabel, isReviewDue, price, todayISO } from "../format";
import type { JournalEntry } from "../types";

const DECISION_LABEL: Record<JournalEntry["decision"], string> = {
  buy: "Buy",
  sell: "Sell",
  hold: "Hold",
  watch: "Watch",
  pass: "Pass",
};

export function JournalEntryCard({
  entry,
  onChange,
  onDelete,
}: {
  entry: JournalEntry;
  onChange: (entry: JournalEntry) => void;
  onDelete: (id: number) => void;
}) {
  const [reviewing, setReviewing] = useState(false);
  const [outcome, setOutcome] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const due = isReviewDue(entry.review_on, entry.outcome_note, todayISO());

  async function saveOutcome(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const updated = await api<JournalEntry>(`/journal/${entry.id}/review`, {
        method: "POST",
        body: JSON.stringify({ outcome_note: outcome }),
      });
      onChange(updated);
      setReviewing(false);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function remove() {
    setError(null);
    try {
      await api(`/journal/${entry.id}`, { method: "DELETE" });
      onDelete(entry.id);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const facts = [
    entry.conviction !== null ? `Conviction ${entry.conviction} of 5` : null,
    entry.price !== null ? `Price ${price(entry.price)}` : null,
    entry.review_on ? `Review on ${dateLabel(entry.review_on)}` : null,
  ].filter(Boolean);

  return (
    <li className="entry">
      <div className="entry-head">
        <h3>
          <Link to={`/report/${entry.ticker}`} className="ticker-link">
            {entry.ticker}
          </Link>{" "}
          <span className="decision">{DECISION_LABEL[entry.decision]}</span>
        </h3>
        <span className="muted small">{dayLabel(entry.created_at)}</span>
      </div>
      <p className="thesis">{entry.thesis}</p>
      {facts.length > 0 && <p className="muted small">{facts.join(", ")}</p>}
      {due && <p className="due">Review due: write down how this turned out.</p>}

      {entry.outcome_note !== null ? (
        <div className="outcome">
          <p className="muted small">Outcome, reviewed {dayLabel(entry.reviewed_at)}</p>
          <p className="thesis">{entry.outcome_note}</p>
        </div>
      ) : reviewing ? (
        <form onSubmit={saveOutcome} className="stack">
          <label htmlFor={`outcome-${entry.id}`}>How did this turn out?</label>
          <textarea
            id={`outcome-${entry.id}`}
            value={outcome}
            onChange={(e) => setOutcome(e.target.value)}
            rows={3}
            autoFocus
          />
          <div className="row-actions">
            <button className="button" disabled={!outcome.trim()}>
              Save outcome
            </button>
            <button type="button" className="link-button" onClick={() => setReviewing(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      <div className="row-actions">
        {entry.outcome_note === null && !reviewing && (
          <button className="link-button" onClick={() => setReviewing(true)}>
            Add outcome
          </button>
        )}
        {confirmDelete ? (
          <>
            <button className="link-button danger" onClick={remove}>
              Confirm delete
            </button>
            <button className="link-button" onClick={() => setConfirmDelete(false)}>
              Keep
            </button>
          </>
        ) : (
          <button className="link-button" onClick={() => setConfirmDelete(true)}>
            Delete
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="error small">
          {error}
        </p>
      )}
    </li>
  );
}
