import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PriceDelta } from "../components/PriceDelta";

describe("PriceDelta", () => {
  it("renders positive value with green color + plus sign prefix", () => {
    render(<PriceDelta value="12.34" />);
    const el = screen.getByText(/\+12\.34 USD/);
    expect(el).toBeInTheDocument();
    expect(el.className).toContain("text-green-400");
  });

  it("renders value verbatim — no Decimal precision loss in DOM (WG#6)", () => {
    // Per WG#6 / OQ-5=A: no Number()/parseFloat for rendering. High-precision
    // Decimal-as-string from backend (e.g., NUMERIC column) must round-trip
    // verbatim — Number() would lose tail digits.
    const highPrecision = "0.000000123456789";
    render(<PriceDelta value={highPrecision} />);
    // data-value attribute holds the original verbatim string.
    const el = document.querySelector(`[data-value="${highPrecision}"]`);
    expect(el).not.toBeNull();
    expect(el?.textContent).toContain(highPrecision);
  });
});
