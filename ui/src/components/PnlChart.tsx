// T-413 — Recharts wrapper for cumulative P&L line chart. Per BRIEF
// §14.3:2061 + §14.4 component library convention. Per WG#5: X-axis
// tick formatter uses browser-local TZ (F5+ CEST/UTC toggle will
// replace per BRIEF §14.2). Per WG#8: `decimalToChartNumber` is the
// SINGLE point of policy for §5.13 statistical-viz Decimal→float
// cast — verbatim Decimal preserved in tooltip; float cast acceptable
// for axis-pixel positioning only.

import * as React from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { PnlSeriesPoint } from "@/lib/api-types";

interface PnlChartProps {
  data: PnlSeriesPoint[];
  height?: number;
}

// §5.13 / §5.3 verbatim Decimal preserved in tooltip; float cast
// acceptable for axis-pixel positioning only. Chart axes are
// statistical visualization, not financial primitives.
function decimalToChartNumber(value: string): number {
  return parseFloat(value);
}

interface ChartPoint {
  bucket_at: string;
  cumulative: number;
  raw: string;
}

function formatTick(iso: string): string {
  // Browser-local TZ for chart tick; F5+ CEST/UTC toggle will replace
  // per BRIEF §14.2. Hour:minute compact format keeps axis labels short.
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function PnlChart({ data, height = 240 }: PnlChartProps): React.JSX.Element {
  if (data.length === 0) {
    return (
      <div
        data-testid="pnl-chart-empty"
        className="flex items-center justify-center text-sm text-muted-foreground"
        style={{ height }}
      >
        No P&L data
      </div>
    );
  }

  const chartData: ChartPoint[] = data.map((p) => ({
    bucket_at: p.bucket_at,
    cumulative: decimalToChartNumber(p.cumulative_pnl),
    raw: p.cumulative_pnl,
  }));

  return (
    <div data-testid="pnl-chart" style={{ height, width: "100%" }}>
      <ResponsiveContainer>
        <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis
            dataKey="bucket_at"
            tickFormatter={formatTick}
            stroke="hsl(var(--muted-foreground))"
            fontSize={11}
          />
          <YAxis
            stroke="hsl(var(--muted-foreground))"
            fontSize={11}
            tickFormatter={(v: number) => v.toFixed(2)}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "hsl(var(--card))",
              border: "1px solid hsl(var(--border))",
              fontSize: 12,
            }}
            formatter={(value: number, _name: string, item: { payload?: ChartPoint }) => [
              item.payload?.raw ?? String(value),
              "Cumulative P&L",
            ]}
            labelFormatter={(label: string) => new Date(label).toLocaleString()}
          />
          <Line
            type="monotone"
            dataKey="cumulative"
            stroke="hsl(var(--primary))"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
