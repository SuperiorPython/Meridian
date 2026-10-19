export type CategoryKey = "growth" | "profitability" | "financial_health" | "valuation";
export type Rating = "Strong" | "Moderate" | "Weak" | "Inexpensive" | "Elevated" | "Unavailable";

export interface Signal {
  id: string;
  label: string;
  value: number | null;
  points: number | null; // 0, 0.5, 1; null = shown but not scored
  finding: string;
  rule: string;
}

export interface Category {
  title: string;
  rating: Rating;
  score: number | null; // 0-100
  signals: Signal[];
}

export interface DataQualityNote {
  level: "warn" | "info";
  text: string;
}

export interface KeyFigures {
  latest_fiscal_year_end: string | null;
  latest_fiscal_year_available_from: string | null;
  ttm_through: string | null;
  price_date: string | null;
  close: number | null;
  market_cap: number | null;
  pe: number | null;
  fcf_yield: number | null;
}

export interface Report {
  ticker: string;
  entity_name: string | null;
  cik: number | null;
  sic: number | null;
  sic_description: string | null;
  is_financial: boolean;
  as_of: string;
  summary: string;
  categories: Record<CategoryKey, Category>;
  key_figures: KeyFigures;
  data_quality: DataQualityNote[];
  method: { scoring: string; disclaimer: string };
}

export interface AuthStatus {
  authenticated: boolean;
  auth_enabled: boolean;
}

export type Decision = "buy" | "sell" | "hold" | "watch" | "pass";

export interface WatchlistItem {
  ticker: string;
  note: string;
  added_at: string;
}

export interface JournalEntry {
  id: number;
  ticker: string;
  decision: Decision;
  thesis: string;
  conviction: number | null;
  price: number | null;
  review_on: string | null;
  created_at: string;
  outcome_note: string | null;
  reviewed_at: string | null;
}
