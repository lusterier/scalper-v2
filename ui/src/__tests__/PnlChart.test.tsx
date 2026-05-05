import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PnlChart } from "../components/PnlChart";

// Recharts ResponsiveContainer relies on `getBoundingClientRect`, which
// jsdom returns as {width:0, height:0} → chart never renders. Mock it
// to a fixed width/height so the LineChart child mounts.
vi.mock("recharts", async (importActual) => {
  const actual = (await importActual()) as Record<string, unknown>;
  const FixedSizeContainer = ({ children }: { children: React.ReactNode }): React.JSX.Element => (
    <div style={{ width: 600, height: 240 }} data-testid="responsive-container">
      {children}
    </div>
  );
  return { ...actual, ResponsiveContainer: FixedSizeContainer };
});

describe("PnlChart", () => {
  it("renders LineChart with correct data points", () => {
    const data = [
      { bucket_at: "2026-05-05T00:00:00Z", bucket_pnl: "1.00", cumulative_pnl: "1.00" },
      { bucket_at: "2026-05-05T01:00:00Z", bucket_pnl: "2.00", cumulative_pnl: "3.00" },
      { bucket_at: "2026-05-05T02:00:00Z", bucket_pnl: "1.50", cumulative_pnl: "4.50" },
    ];
    render(<PnlChart data={data} />);
    expect(screen.getByTestId("pnl-chart")).toBeInTheDocument();
    expect(screen.getByTestId("responsive-container")).toBeInTheDocument();
  });

  it("renders 'No P&L data' placeholder when data=[]", () => {
    render(<PnlChart data={[]} />);
    expect(screen.getByTestId("pnl-chart-empty")).toBeInTheDocument();
    expect(screen.getByText("No P&L data")).toBeInTheDocument();
  });
});
