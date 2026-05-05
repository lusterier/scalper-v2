import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FeatureSnapshotTable } from "../components/FeatureSnapshotTable";

describe("FeatureSnapshotTable", () => {
  it("renders sorted key-value rows from snapshot dict (per WG#9 + WG#10 null handling)", () => {
    // Includes null value to verify WG#10 — render literal "null" with muted tone.
    const snapshot = {
      "ind.btcusdt.15m.rsi_14": 42.5,
      "ind.btcusdt.15m.ema_20": 50_000.123,
      "ind.btcusdt.15m.is_above_ma": true,
      "ind.btcusdt.15m.funding_rate": null,
    };
    render(<FeatureSnapshotTable snapshot={snapshot} />);
    const rows = screen.getAllByTestId("feature-snapshot-row");
    expect(rows).toHaveLength(4);
    // Verify alphabetical sort — first row is "ema_20" not "rsi_14".
    expect(rows[0]).toHaveTextContent("ema_20");
    expect(rows[0]).toHaveTextContent("50000.123000");
    // null value renders literal "null" string, NOT placeholder.
    const nullRow = rows.find((r) => r.textContent?.includes("funding_rate"));
    expect(nullRow).toBeDefined();
    expect(nullRow!.textContent).toMatch(/null/);
  });

  it("renders '(empty feature_snapshot)' placeholder when snapshot={} (per WG#9)", () => {
    render(<FeatureSnapshotTable snapshot={{}} />);
    expect(screen.getByTestId("feature-snapshot-empty")).toHaveTextContent(
      "(empty feature_snapshot)",
    );
    expect(screen.queryByTestId("feature-snapshot-table")).not.toBeInTheDocument();
  });

  it("handles nested object/array via JSON.stringify with 80-char truncation", () => {
    const longArray = Array.from({ length: 50 }, (_, i) => i);
    const snapshot = {
      simple: "value",
      nested: { foo: 1, bar: [1, 2, 3] },
      long_array: longArray,
    };
    render(<FeatureSnapshotTable snapshot={snapshot} />);
    const rows = screen.getAllByTestId("feature-snapshot-row");
    expect(rows).toHaveLength(3);
    // Long array row truncated with ellipsis.
    const longRow = rows.find((r) => r.textContent?.includes("long_array"));
    expect(longRow).toBeDefined();
    expect(longRow!.textContent).toContain("…");
    // Tooltip via title attr exposes full JSON.
    const longCell = longRow!.querySelector("td[title]");
    expect(longCell).toBeDefined();
    expect(longCell!.getAttribute("title")?.length ?? 0).toBeGreaterThan(80);
  });
});
