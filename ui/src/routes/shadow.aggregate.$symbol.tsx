// T-517a2 — per-symbol best-variant aggregate detail (BRIEF §13.6 second
// bullet "which variant would have been best over last N trades?").
// Mirror shadow.rejected.tsx filter card + DataTable shape modulo path-param
// symbol + 9-col aggregate-metric layout + 'Best' pill on first row + no
// pagination (variant counts are bounded ~5 per symbol per parent trade).
//
// Time range filter ALWAYS applies (NO omit-when-X heuristic; mirror
// T-517b2 shadow.rejected.tsx WG#4): backend filters created_at column
// which is NON-NULL per migration 0014.

import { type ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { BotSelector } from "@/components/BotSelector";
import { DataTable } from "@/components/DataTable";
import { PriceDelta } from "@/components/PriceDelta";
import { type TimeRange, TimeRangePicker } from "@/components/TimeRangePicker";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api-client";
import type {
  VariantAggregate,
  VariantAggregateListResponse,
} from "@/lib/api-types";

export const Route = createFileRoute("/shadow/aggregate/$symbol")({
  component: ShadowAggregateSymbolPage,
});

const WINDOW_30D_MS = 30 * 24 * 60 * 60 * 1000;

interface Filters {
  botId: string;
  range: TimeRange;
}

function buildAggregateUrl(symbol: string, filters: Filters): string {
  const params: string[] = [];
  if (filters.botId !== "") {
    params.push(`bot_id=${encodeURIComponent(filters.botId)}`);
  }
  // Time range always applies: backend filters created_at (non-null per
  // migration 0014). Mirror T-517b2 shadow.rejected.tsx WG#4 inversion of
  // paper-trades.index omit-when-active heuristic.
  params.push(`from=${encodeURIComponent(filters.range.from.toISOString())}`);
  params.push(`to=${encodeURIComponent(filters.range.to.toISOString())}`);
  return `/api/shadow/aggregate/${encodeURIComponent(symbol)}?${params.join("&")}`;
}

function ShadowAggregateSymbolPage(): React.JSX.Element {
  const { symbol } = Route.useParams();
  const [filters, setFilters] = React.useState<Filters>(() => {
    const now = new Date();
    return {
      botId: "",
      range: { from: new Date(now.getTime() - WINDOW_30D_MS), to: now, preset: "30d" },
    };
  });

  const aggregateQuery = useQuery({
    queryKey: ["shadow-aggregate", symbol, filters],
    queryFn: () =>
      apiFetch<VariantAggregateListResponse>(buildAggregateUrl(symbol, filters)),
  });

  const variants = aggregateQuery.data?.variants ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            Variant aggregate — <span className="font-mono">{symbol}</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <BotSelector
            value={filters.botId}
            onChange={(v) => {
              setFilters((f) => ({ ...f, botId: typeof v === "string" ? v : "" }));
            }}
            placeholder="All bots"
          />
          <div title="Filters by created_at (variant termination time)">
            <TimeRangePicker
              value={filters.range}
              onChange={(range) => {
                setFilters((f) => ({ ...f, range }));
              }}
            />
          </div>
        </CardContent>
      </Card>

      {aggregateQuery.isLoading ? (
        <div className="text-muted-foreground">Loading…</div>
      ) : aggregateQuery.isError ? (
        <div className="text-red-400 text-sm">Failed to load variant aggregate</div>
      ) : (
        <DataTable
          columns={VARIANT_COLUMNS}
          data={variants}
          pageSize={50}
          enableColumnVisibility={false}
          enableFiltering={false}
          enableSorting={false}
          emptyMessage={`No variants for ${symbol} in selected window`}
        />
      )}
    </div>
  );
}

function BestPill(): React.JSX.Element {
  return (
    <span
      data-testid="aggregate-best-pill"
      className={
        "inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium " +
        "bg-primary/15 text-primary"
      }
    >
      Best
    </span>
  );
}

// Mirror shadow.rejected.tsx formatPct + ShadowVariantsView.tsx formatPctPair
// pattern; extract to lib/format-pct.ts at 4th consumer (currently 3 — under
// rule-of-three threshold per WG#4).
function formatPct(n: number | null): React.JSX.Element {
  if (n === null) {
    return <span className="text-muted-foreground">—</span>;
  }
  const colour = n >= 0 ? "text-green-400" : "text-red-400";
  return <span className={`font-mono text-xs ${colour}`}>{`${(n * 100).toFixed(2)}%`}</span>;
}

// Mirror shadow.rejected.tsx formatPct + ShadowVariantsView.tsx formatPctPair
// pattern; extract to lib/format-pct.ts at 4th consumer. Plain percent
// without sign-color since win_rate is always non-negative ratio in [0, 1].
function formatPctNumber(n: number): React.JSX.Element {
  return <span className="font-mono text-xs">{`${(n * 100).toFixed(1)}%`}</span>;
}

const VARIANT_COLUMNS: ColumnDef<VariantAggregate, unknown>[] = [
  {
    accessorKey: "variant_name",
    header: "Variant",
    // Best pill on first row only (sorted DESC by total_pnl per backend;
    // contract pinned by services/analytics_api/app/analytics_compute.py:389
    // sorted(metrics, key=lambda m: (-m.total_pnl, m.variant_name)) +
    // tests/test_analytics_compute.py::test_compute_variant_aggregate_sorted_by_total_pnl_desc_tiebreak_variant_name_asc).
    // Future backend refactor that changes sort key would silently mis-label
    // without this load-bearing reference.
    cell: ({ row }) => (
      <span className="inline-flex items-center gap-2">
        <span className="font-mono text-xs">{row.original.variant_name}</span>
        {row.index === 0 && <BestPill />}
      </span>
    ),
  },
  { accessorKey: "n_trades", header: "Trades" },
  { accessorKey: "win_count", header: "Wins" },
  {
    accessorKey: "win_rate",
    header: "Win %",
    cell: ({ row }) => formatPctNumber(row.original.win_rate),
  },
  {
    accessorKey: "total_pnl",
    header: "Total P&L",
    cell: ({ row }) => <PriceDelta value={row.original.total_pnl} />,
  },
  {
    accessorKey: "avg_pnl",
    header: "Avg P&L",
    cell: ({ row }) => <PriceDelta value={row.original.avg_pnl} />,
  },
  {
    accessorKey: "best_pnl",
    header: "Best",
    cell: ({ row }) => <PriceDelta value={row.original.best_pnl} />,
  },
  {
    accessorKey: "worst_pnl",
    header: "Worst",
    cell: ({ row }) => <PriceDelta value={row.original.worst_pnl} />,
  },
  {
    accessorKey: "avg_mfe_pct",
    header: "Avg MFE %",
    cell: ({ row }) => formatPct(row.original.avg_mfe_pct),
  },
  {
    accessorKey: "avg_mae_pct",
    header: "Avg MAE %",
    cell: ({ row }) => formatPct(row.original.avg_mae_pct),
  },
];
