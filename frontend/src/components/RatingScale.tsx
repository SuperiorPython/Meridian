import { MODERATE_CUTOFF, STRONG_CUTOFF, ratingTone, zoneLabels } from "../format";
import type { CategoryKey, Rating } from "../types";

/** A 0-100 scale with the two cutoffs marked, so a score is read against the rule
 * that produced the label rather than as a bare number. */
export function RatingScale({
  categoryKey,
  rating,
  score,
}: {
  categoryKey: CategoryKey;
  rating: Rating;
  score: number | null;
}) {
  const [low, mid, high] = zoneLabels(categoryKey);
  const tone = ratingTone(rating);
  const label =
    score === null ? "Not enough data to rate" : `Score ${score} out of 100, rated ${rating}`;

  return (
    <div className="scale" role="img" aria-label={label}>
      <div className={`scale-track${score === null ? " empty" : ""}`}>
        <span className="tick" style={{ left: `${MODERATE_CUTOFF}%` }} />
        <span className="tick" style={{ left: `${STRONG_CUTOFF}%` }} />
        {score !== null && (
          <span className={`marker tone-${tone}`} style={{ left: `${Math.min(100, Math.max(0, score))}%` }} />
        )}
      </div>
      <div className="scale-zones" aria-hidden="true">
        <span style={{ left: 0 }}>{low}</span>
        <span style={{ left: `${MODERATE_CUTOFF}%` }}>{mid}</span>
        <span style={{ left: `${STRONG_CUTOFF}%` }}>{high}</span>
      </div>
    </div>
  );
}
