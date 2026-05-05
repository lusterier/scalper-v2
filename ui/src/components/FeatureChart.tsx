// T-417 — Recharts wrapper for feature historical values. Per OQ-2=B:
// only numeric features render here (consumer route filters by
// value_num !== null before mounting). Mirrors T-413 PnlChart contract
// but X-axis = computed_at + Y-axis = value_num (float per §5.13
// statistical metric, NOT money).

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

import type { FeatureRow } from "@/lib/api-types";

interface FeatureChartProps {
  data: FeatureRow[];
  height?: number;
}

interface ChartPoint {
  computed_at: string;
  value: number;
}

function formatTick(iso: string): string {
  // Browser-local TZ for chart tick; F5+ CEST/UTC toggle will replace
  // per BRIEF §14.2. Hour:minute compact label.
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function FeatureChart({ data, height = 240 }: FeatureChartProps): React.JSX.Element {
  // Per WG#7 — filter null `value_num` rows BEFORE Recharts mapping.
  // Reverse to ascending order (backend returns DESC; chart wants ASC).
  const chartData: ChartPoint[] = data
    .filter((r): r is FeatureRow & { value_num: number } => r.value_num !== null)
    .map((r) => ({ computed_at: r.computed_at, value: r.value_num }))
    .reverse();

  if (chartData.length === 0) {
    return (
      <div
        data-testid="feature-chart-empty"
        className="flex items-center justify-center text-sm text-muted-foreground"
        style={{ height }}
      >
        No numeric data
      </div>
    );
  }

  return (
    <div data-testid="feature-chart" style={{ height, width: "100%" }}>
      <ResponsiveContainer>
        <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis
            dataKey="computed_at"
            tickFormatter={formatTick}
            stroke="hsl(var(--muted-foreground))"
            fontSize={11}
          />
          <YAxis
            stroke="hsl(var(--muted-foreground))"
            fontSize={11}
            tickFormatter={(v: number) => v.toFixed(4)}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "hsl(var(--card))",
              border: "1px solid hsl(var(--border))",
              fontSize: 12,
            }}
            formatter={(value: number) => [value.toFixed(6), "value"]}
            labelFormatter={(label: string) => new Date(label).toLocaleString()}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke="hsl(var(--primary))"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
