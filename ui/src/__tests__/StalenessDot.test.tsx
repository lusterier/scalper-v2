import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { STALENESS_MS, StalenessDot } from "../components/StalenessDot";

describe("StalenessDot", () => {
  it("renders green when computedAt is within threshold (fresh)", () => {
    const recent = new Date(Date.now() - 60_000).toISOString(); // 1 min ago
    render(<StalenessDot computedAt={recent} />);
    const dot = screen.getByTestId("staleness-dot");
    expect(dot.getAttribute("data-status")).toBe("fresh");
    expect(dot.className).toContain("bg-green-500");
  });

  it("renders red when computedAt exceeds threshold (stale)", () => {
    const old = new Date(Date.now() - STALENESS_MS - 60_000).toISOString();
    render(<StalenessDot computedAt={old} />);
    const dot = screen.getByTestId("staleness-dot");
    expect(dot.getAttribute("data-status")).toBe("stale");
    expect(dot.className).toContain("bg-red-500");
  });

  it("respects thresholdMs prop override", () => {
    const twoMinAgo = new Date(Date.now() - 2 * 60_000).toISOString();
    // With default 5min threshold → fresh; with 1min override → stale.
    const { rerender } = render(<StalenessDot computedAt={twoMinAgo} />);
    expect(screen.getByTestId("staleness-dot").getAttribute("data-status")).toBe(
      "fresh",
    );
    rerender(<StalenessDot computedAt={twoMinAgo} thresholdMs={60_000} />);
    expect(screen.getByTestId("staleness-dot").getAttribute("data-status")).toBe(
      "stale",
    );
  });
});
