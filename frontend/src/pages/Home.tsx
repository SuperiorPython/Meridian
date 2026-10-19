import { TickerForm } from "../components/TickerForm";

export function Home() {
  return (
    <section className="reading">
      <h1 className="title">Look up a company</h1>
      <p className="lede">
        Enter a US stock ticker to read a report built from its SEC filings: growth, profitability,
        financial health and valuation, with the numbers behind every rating.
      </p>
      <TickerForm />
    </section>
  );
}
