import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { CategorySection } from "../components/CategorySection";
import { KeyFigures } from "../components/KeyFigures";
import type { CategoryKey, Report } from "../types";

const ORDER: CategoryKey[] = ["growth", "profitability", "financial_health", "valuation"];

type State =
  | { status: "loading" }
  | { status: "error"; code: number; message: string }
  | { status: "ready"; report: Report };

export function ReportPage() {
  const { ticker = "" } = useParams();
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    api<Report>(`/report/${encodeURIComponent(ticker)}`, { signal: controller.signal })
      .then((report) => setState({ status: "ready", report }))
      .catch((err: unknown) => {
        if ((err as Error).name === "AbortError") return;
        const e = err as ApiError;
        setState({ status: "error", code: e.status ?? 0, message: e.message });
      });
    return () => controller.abort();
  }, [ticker]);

  useEffect(() => {
    document.title = `${ticker.toUpperCase()} · Meridian`;
    return () => {
      document.title = "Meridian";
    };
  }, [ticker]);

  if (state.status === "loading") {
    return (
      <section className="reading" aria-live="polite">
        <h1 className="title">{ticker.toUpperCase()}</h1>
        <p className="muted">
          Reading filings and prices. The first lookup of a company can take several seconds; later ones
          are quick.
        </p>
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section className="reading">
        <h1 className="title">No report for {ticker.toUpperCase()}</h1>
        <p role="alert" className="error">
          {state.message}
        </p>
        <p className="muted">
          {state.code === 404 || state.code === 422
            ? "Check the ticker symbol. Only companies that file with the SEC are covered."
            : state.code === 503
              ? "This is a server setting, not a problem with the ticker. Fix it in the server's .env file, then restart uvicorn."
              : "This is usually temporary. Try again in a moment."}
        </p>
        <Link to="/">Look up another company</Link>
      </section>
    );
  }

  const { report } = state;
  const warnings = report.data_quality.filter((n) => n.level === "warn");
  return (
    <article className="reading">
      <h1 className="title">{report.entity_name ?? report.ticker}</h1>
      <p className="muted subtitle">
        {report.ticker}
        {report.sic_description ? `, ${report.sic_description}` : ""}
      </p>
      <p className="lede">{report.summary}</p>

      {warnings.length > 0 && (
        <p className="notice">
          {warnings.length === 1 ? "1 data warning" : `${warnings.length} data warnings`} below affect how far
          to trust these ratings.
        </p>
      )}

      <h2>Ratings</h2>
      <p className="muted small hint">Open a rating to see the numbers and the rule behind it.</p>
      <div className="categories">
        {ORDER.map((key) => (
          <CategorySection key={key} id={key} category={report.categories[key]} />
        ))}
      </div>

      <h2>Key figures</h2>
      <KeyFigures figures={report.key_figures} />

      <h2>Data quality</h2>
      {report.data_quality.length === 0 ? (
        <p className="muted">Nothing unusual found in this company's filings.</p>
      ) : (
        <ul className="quality">
          {report.data_quality.map((n, i) => (
            <li key={i} className={n.level}>
              <strong>{n.level === "warn" ? "Check this: " : "For context: "}</strong>
              {n.text}
            </li>
          ))}
        </ul>
      )}

      <details className="method">
        <summary>How ratings work</summary>
        <p>{report.method.scoring}</p>
      </details>
      <p className="disclaimer muted small">
        {report.method.disclaimer} Report as of {report.as_of}.
      </p>
    </article>
  );
}
