import { compactMoney, dateLabel, multiple, percent, price } from "../format";
import type { KeyFigures as Figures } from "../types";

export function KeyFigures({ figures }: { figures: Figures }) {
  const items: [string, string, string?][] = [
    ["Share price", price(figures.close), figures.price_date ? `on ${dateLabel(figures.price_date)}` : undefined],
    ["Market value", compactMoney(figures.market_cap)],
    ["Price to earnings", multiple(figures.pe)],
    ["Free cash flow yield", percent(figures.fcf_yield)],
    [
      "Latest fiscal year",
      dateLabel(figures.latest_fiscal_year_end),
      figures.latest_fiscal_year_available_from
        ? `filed ${dateLabel(figures.latest_fiscal_year_available_from)}`
        : undefined,
    ],
    ["Trailing twelve months", figures.ttm_through ? `to ${dateLabel(figures.ttm_through)}` : "Not available"],
  ];
  return (
    <dl className="figures">
      {items.map(([label, value, note]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
          {note && <dd className="muted small">{note}</dd>}
        </div>
      ))}
    </dl>
  );
}
