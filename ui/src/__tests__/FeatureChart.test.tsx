import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FeatureChart } from "../components/FeatureChart";

vi.mock("recharts", async (importActual) => {
  const actual = (await importActual()) as Record<string, unknown>;
  const FixedSizeContainer = ({ children }: { children: React.ReactNode }): React.JSX.Element => (
    <div style={{ width: 600, height: 240 }} data-testid="responsive-container">
      {children}
    </div>
  );
  return { ...actual, ResponsiveContainer: FixedSizeContainer };
});

describe("FeatureChart", () => {
  it("renders LineChart with numeric value_num data points", () => {
    const data = [
      {
        feature_name: "ind.btcusdt.15m.ema_20",
        symbol: "BTCUSDT",
        computed_at: "2026-05-05T00:00:00Z",
        value_num: 50_000,
        value_bool: null,
        value_json: null,
        source_version: "1.0",
      },
      {
        feature_name: "ind.btcusdt.15m.ema_20",
        symbol: "BTCUSDT",
        computed_at: "2026-05-05T01:00:00Z",
        value_num: 50_500,
        value_bool: null,
        value_json: null,
        source_version: "1.0",
      },
    ];
    render(<FeatureChart data={data} />);
    expect(screen.getByTestId("feature-chart")).toBeInTheDocument();
  });

  it("renders 'No numeric data' placeholder when data=[]", () => {
    render(<FeatureChart data={[]} />);
    expect(screen.getByTestId("feature-chart-empty")).toBeInTheDocument();
    expect(screen.getByText("No numeric data")).toBeInTheDocument();
  });

  it("filters out rows where value_num is null (does not render in chart) per WG#7", () => {
    const data = [
      {
        feature_name: "x",
        symbol: "BTCUSDT",
        computed_at: "2026-05-05T00:00:00Z",
        value_num: 1.5,
        value_bool: null,
        value_json: null,
        source_version: "1.0",
      },
      {
        feature_name: "x",
        symbol: "BTCUSDT",
        computed_at: "2026-05-05T00:01:00Z",
        value_num: null,
        value_bool: true,
        value_json: null,
        source_version: "1.0",
      },
      {
        feature_name: "x",
        symbol: "BTCUSDT",
        computed_at: "2026-05-05T00:02:00Z",
        value_num: 2.5,
        value_bool: null,
        value_json: null,
        source_version: "1.0",
      },
    ];
    // 3 rows in but value_num=null filtered out → chart still mounts
    // (NOT empty placeholder).
    render(<FeatureChart data={data} />);
    expect(screen.getByTestId("feature-chart")).toBeInTheDocument();
    expect(screen.queryByTestId("feature-chart-empty")).not.toBeInTheDocument();
  });
});
