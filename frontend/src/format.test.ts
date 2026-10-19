import { describe, expect, it } from "vitest";
import {
  compactMoney,
  dateLabel,
  multiple,
  percent,
  price,
  ratingTone,
  signalVerdict,
  zoneLabels,
} from "./format";

describe("format", () => {
  it("compacts money", () => {
    expect(compactMoney(3.21e12)).toBe("$3.21T");
    expect(compactMoney(450e9)).toBe("$450.00B");
    expect(compactMoney(12.5e6)).toBe("$12.50M");
    expect(compactMoney(9500)).toBe("$9,500");
    expect(compactMoney(null)).toBe("–");
  });

  it("formats prices, multiples and percentages", () => {
    expect(price(190.5)).toBe("$190.50");
    expect(multiple(27.24)).toBe("27.2×");
    expect(percent(0.0512)).toBe("5.1%");
    expect(percent(-0.2, 0)).toBe("-20%");
    expect(price(undefined)).toBe("–");
  });

  it("formats dates without shifting by timezone", () => {
    expect(dateLabel("2025-09-27")).toBe("Sep 27, 2025");
    expect(dateLabel(null)).toBe("–");
    expect(dateLabel("garbage")).toBe("garbage");
  });

  it("maps ratings to tones, valuation included", () => {
    expect(ratingTone("Strong")).toBe("good");
    expect(ratingTone("Inexpensive")).toBe("good");
    expect(ratingTone("Elevated")).toBe("low");
    expect(ratingTone("Weak")).toBe("low");
    expect(ratingTone("Moderate")).toBe("mid");
    expect(ratingTone("Unavailable")).toBe("none");
  });

  it("describes signal results", () => {
    expect(signalVerdict(1).tone).toBe("good");
    expect(signalVerdict(0.5).tone).toBe("mid");
    expect(signalVerdict(0).tone).toBe("low");
    expect(signalVerdict(null).text).toBe("Not scored");
  });

  it("reverses zone labels for valuation only", () => {
    expect(zoneLabels("growth")).toEqual(["Weak", "Moderate", "Strong"]);
    expect(zoneLabels("valuation")).toEqual(["Elevated", "Moderate", "Inexpensive"]);
  });
});
