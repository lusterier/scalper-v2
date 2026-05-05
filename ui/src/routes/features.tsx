// T-417 — Section 6 Feature inspector. Per BRIEF §14.3:2065. Feature
// browser (prefix filter) + click row → chart historical values for
// numeric features OR placeholder + history table for non-numeric
// (per OQ-2=B). StalenessDot per OQ-3=A using STALENESS_MS = 5min.

import { type ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { DataTable } from "@/components/DataTable";
import { FeatureChart } from "@/components/FeatureChart";
import { StalenessDot } from "@/components/StalenessDot";
import { type TimeRange, TimeRangePicker } from "@/components/TimeRangePicker";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api-client";
import type {
  FeatureHistoryListResponse,
  FeatureLatestListResponse,
  FeatureRow,
} from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

export const Route = createFileRoute("/features")({
  component: FeatureInspectorPage,
});

// Per L-001 + §N9 — named constants. STALE_MS matches global staleTime
// from main.tsx; LATEST_PAGE_SIZE / HISTORY_LIMIT mirror backend
// defaults from services/analytics_api/app/routers/features.py.
const STALE_MS = 30_000;
const LATEST_PAGE_SIZE = 100;
const HISTORY_LIMIT = 1000;
const WINDOW_24H_MS = 24 * 60 * 60 * 1000;

interface SelectedFeature {
  feature_name: string;
  symbol: string;
  isNumeric: boolean;
}

function buildLatestUrl(prefix: string): string {
  // Per WG#3 — empty prefix omits ?prefix= entirely (mirrors T-414
  // status="all" precedent). Backend treats absent prefix as no filter.
  if (prefix.trim() === "") {
    return `/api/features/latest?limit=${String(LATEST_PAGE_SIZE)}`;
  }
  return `/api/features/latest?prefix=${encodeURIComponent(prefix)}&limit=${String(LATEST_PAGE_SIZE)}`;
}

function buildHistoryUrl(
  selected: SelectedFeature,
  fromIso: string,
  toIso: string,
): string {
  return (
    `/api/features/history` +
    `?feature_name=${encodeURIComponent(selected.feature_name)}` +
    `&symbol=${encodeURIComponent(selected.symbol)}` +
    `&from=${encodeURIComponent(fromIso)}` +
    `&to=${encodeURIComponent(toIso)}` +
    `&limit=${String(HISTORY_LIMIT)}`
  );
}

function FeatureInspectorPage(): React.JSX.Element {
  const [prefix, setPrefix] = React.useState("");
  const [selected, setSelected] = React.useState<SelectedFeature | null>(null);
  const [range, setRange] = React.useState<TimeRange>(() => {
    const now = new Date();
    return { from: new Date(now.getTime() - WINDOW_24H_MS), to: now, preset: "24h" };
  });

  const fromIso = React.useMemo(() => range.from.toISOString(), [range.from]);
  const toIso = React.useMemo(() => range.to.toISOString(), [range.to]);

  const latestQuery = useQuery({
    queryKey: ["features-latest", prefix],
    queryFn: () => apiFetch<FeatureLatestListResponse>(buildLatestUrl(prefix)),
    staleTime: STALE_MS,
  });

  const historyQuery = useQuery({
    queryKey: ["feature-history", selected, fromIso, toIso],
    queryFn: () =>
      apiFetch<FeatureHistoryListResponse>(buildHistoryUrl(selected!, fromIso, toIso)),
    // Per WG#4 — history fetch ONLY fires when feature selected.
    enabled: selected !== null,
    staleTime: STALE_MS,
  });

  const handleRowClick = (row: FeatureRow): void => {
    setSelected({
      feature_name: row.feature_name,
      symbol: row.symbol,
      isNumeric: row.value_num !== null,
    });
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Filters</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Input
            data-testid="prefix-input"
            placeholder="Feature name prefix (e.g. ind.btcusdt.15m)"
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
            className="h-10 w-[320px]"
          />
          <TimeRangePicker value={range} onChange={setRange} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Feature browser</CardTitle>
        </CardHeader>
        <CardContent>
          {latestQuery.isLoading ? (
            <div className="text-muted-foreground">Loading…</div>
          ) : latestQuery.isError ? (
            <div className="text-red-400 text-sm">Failed to load features</div>
          ) : (
            <DataTable
              columns={LATEST_COLUMNS}
              data={latestQuery.data?.features ?? []}
              pageSize={LATEST_PAGE_SIZE}
              enableColumnVisibility={false}
              enableFiltering={false}
              enableSorting={false}
              onRowClick={handleRowClick}
              emptyMessage="No features match prefix"
            />
          )}
        </CardContent>
      </Card>

      {selected !== null && (
        <Card data-testid="selected-feature-panel">
          <CardHeader>
            <CardTitle className="text-sm">
              {selected.feature_name} · {selected.symbol}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {historyQuery.isLoading ? (
              <span className="text-muted-foreground">Loading history…</span>
            ) : historyQuery.isError ? (
              <span className="text-red-400 text-sm">Failed to load history</span>
            ) : selected.isNumeric ? (
              <FeatureChart data={historyQuery.data?.features ?? []} />
            ) : (
              <NonNumericHistory data={historyQuery.data?.features ?? []} />
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function NonNumericHistory({ data }: { data: FeatureRow[] }): React.JSX.Element {
  if (data.length === 0) {
    return (
      <div data-testid="non-numeric-empty" className="text-sm text-muted-foreground">
        No history data
      </div>
    );
  }
  return (
    <div data-testid="non-numeric-history" className="space-y-2">
      <div className="text-xs text-muted-foreground">
        Not chartable — JSON/bool view
      </div>
      <ul className="divide-y divide-border rounded-md border border-border">
        {data.slice(0, 100).map((row) => (
          <li
            key={`${row.computed_at}-${row.feature_name}-${row.symbol}`}
            className="flex items-center gap-3 px-3 py-1.5 text-xs"
          >
            <span className="font-mono text-muted-foreground">
              {formatUtcDateTime(row.computed_at)}
            </span>
            <span className="font-mono">{renderRawValue(row)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function renderValue(row: FeatureRow): React.ReactNode {
  // Per WG#2 — `!== null` not falsy; `value_num=0` and `value_bool=false`
  // are valid renderable values, not placeholders. Per WG#9 — unified
  // "JSON" placeholder (no per-shape branching dict-vs-array).
  if (row.value_num !== null) {
    return <span className="font-mono">{row.value_num.toFixed(4)}</span>;
  }
  if (row.value_bool !== null) {
    return <span className="font-mono">{String(row.value_bool)}</span>;
  }
  if (row.value_json !== null) {
    return <span className="text-xs text-muted-foreground">JSON</span>;
  }
  return <span className="text-muted-foreground">—</span>;
}

function renderRawValue(row: FeatureRow): string {
  if (row.value_num !== null) return row.value_num.toFixed(6);
  if (row.value_bool !== null) return String(row.value_bool);
  if (row.value_json !== null) return JSON.stringify(row.value_json);
  return "—";
}

const LATEST_COLUMNS: ColumnDef<FeatureRow, unknown>[] = [
  { accessorKey: "feature_name", header: "Feature" },
  { accessorKey: "symbol", header: "Symbol" },
  {
    accessorKey: "value_num",
    header: "Value",
    cell: ({ row }) => renderValue(row.original),
  },
  {
    accessorKey: "computed_at",
    header: "Computed at",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{formatUtcDateTime(row.original.computed_at)}</span>
    ),
  },
  {
    id: "freshness",
    header: "Status",
    cell: ({ row }) => <StalenessDot computedAt={row.original.computed_at} />,
  },
];
