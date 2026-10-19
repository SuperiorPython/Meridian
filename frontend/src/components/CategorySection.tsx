import { ratingTone, signalVerdict } from "../format";
import type { Category, CategoryKey } from "../types";
import { RatingScale } from "./RatingScale";

export function CategorySection({ id, category }: { id: CategoryKey; category: Category }) {
  const tone = ratingTone(category.rating);
  const scored = category.signals.filter((s) => s.points !== null).length;
  return (
    <details className="category">
      <summary>
        <span className="category-name">
          <span className="category-title">
            <span className="chevron" aria-hidden="true" />
            {category.title}
          </span>
          <span className="muted small">
            {category.signals.length} {category.signals.length === 1 ? "signal" : "signals"}, {scored} scored
          </span>
        </span>
        <span className={`rating tone-${tone}`}>{category.rating}</span>
        <RatingScale categoryKey={id} rating={category.rating} score={category.score} />
      </summary>
      {category.signals.length === 0 ? (
        <p className="muted">No signals could be computed from the available filings.</p>
      ) : (
        <ul className="signals">
          {category.signals.map((s) => {
            const verdict = signalVerdict(s.points);
            return (
              <li key={s.id}>
                <div className="signal-head">
                  <span className="signal-label">{s.label}</span>
                  <span className={`verdict tone-${verdict.tone}`}>{verdict.text}</span>
                </div>
                <p className="finding">{s.finding}</p>
                <p className="rule muted small">{s.rule}</p>
              </li>
            );
          })}
        </ul>
      )}
    </details>
  );
}
