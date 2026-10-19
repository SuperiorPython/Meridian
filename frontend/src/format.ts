import type { CategoryKey, Rating } from "./types";

const DASH = "–";

export function compactMoney(n: number | null | undefined): string {
  if (n == null) return DASH;
  const abs = Math.abs(n);
  const units: [number, string][] = [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
  ];
  for (const [size, suffix] of units) {
    if (abs >= size) return `$${(n / size).toFixed(2)}${suffix}`;
  }
  return `$${Math.round(n).toLocaleString("en-US")}`;
}

export function price(n: number | null | undefined): string {
  return n == null ? DASH : `$${n.toFixed(2)}`;
}

export function multiple(n: number | null | undefined): string {
  return n == null ? DASH : `${n.toFixed(1)}×`;
}

export function percent(n: number | null | undefined, digits = 1): string {
  return n == null ? DASH : `${(n * 100).toFixed(digits)}%`;
}

export function dateLabel(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export type Tone = "good" | "mid" | "low" | "none";

export function ratingTone(rating: Rating): Tone {
  switch (rating) {
    case "Strong":
    case "Inexpensive":
      return "good";
    case "Moderate":
      return "mid";
    case "Weak":
    case "Elevated":
      return "low";
    default:
      return "none";
  }
}

export function signalVerdict(points: number | null): { text: string; tone: Tone } {
  if (points === null) return { text: "Not scored", tone: "none" };
  if (points >= 1) return { text: "Meets the rule", tone: "good" };
  if (points > 0) return { text: "Partly meets it", tone: "mid" };
  return { text: "Misses the rule", tone: "low" };
}

/** Labels for the low / middle / high zones of a 0-100 scale. A high valuation
 * score means less expensive, so its zones read the other way round. */
export function zoneLabels(key: CategoryKey): [string, string, string] {
  return key === "valuation"
    ? ["Elevated", "Moderate", "Inexpensive"]
    : ["Weak", "Moderate", "Strong"];
}

/** Matches the API's cutoffs: 34+ is the middle band, 67+ the top. */
export const MODERATE_CUTOFF = 34;
export const STRONG_CUTOFF = 67;

/** Today's date in the viewer's timezone as YYYY-MM-DD. */
export function todayISO(now: Date = new Date()): string {
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

/** A journal entry is due once its review date arrives and no outcome is written yet. */
export function isReviewDue(reviewOn: string | null, outcome: string | null, today: string): boolean {
  return reviewOn !== null && outcome === null && reviewOn <= today;
}

/** Date part of a stored UTC timestamp, shown in the viewer's timezone. */
export function dayLabel(timestamp: string | null | undefined): string {
  if (!timestamp) return DASH;
  const d = new Date(timestamp);
  if (Number.isNaN(d.getTime())) return timestamp;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
