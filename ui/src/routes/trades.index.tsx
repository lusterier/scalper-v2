// T-414 — Section 3 Trade explorer list. Per BRIEF §14.3:2062.
// Filterable + paginated trade list; click row → drill-down at
// /trades/$tradeId. 4 filters (bot / symbol / status / time-range);
// custom pagination block below DataTable per WG#7.

import { type ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { BotSelector } from "@/components/BotSelector";
import { DataTable } from "@/components/DataTable";
import { PriceDelta } from "@/components/PriceDelta";
import { StatusBadge } from "@/components/StatusBadge";
import { type TimeRange, TimeRangePicker } from "@/components/TimeRangePicker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api-client";
import type { Trade, TradeListResponse } from "@/lib/api-types";

export const Route = createFileRoute("/trades/")({
  component: TradesPage,
});

// Per WG#5 — named constants document rationale (avoid L-001 magic-
// numbers scan). 50 = backend default per services/analytics_api/app/
// routers/trades.py:_DEFAULT_LIMIT.
const PAGE_SIZE = 50;
const WINDOW_30D_MS = 30 * 24 * 60 * 60 * 1000;

type StatusFilter = "all" | "open" | "closed" | "error";

interface Filters {
  botId: string;
  symbol: string;
  status: StatusFilter;
  range: TimeRange;
}

// Per WG#4 — buildTradesUrl skips ?from= + ?to= when status="open"
// because backend filters closed_at column (open trades have null
// closed_at; time-range yields empty set without OMIT). status="all"
// omits ?status= entirely (backend default treats absent as all).
function buildTradesUrl(filters: Filters, limit: number, offset: number): string {
  const params: string[] = [];
  if (filters.botId !== "") {
    params.push(`bot_id=${encodeURIComponent(filters.botId)}`);
  }
  if (filters.symbol !== "") {
    params.push(`symbol=${encodeURIComponent(filters.symbol)}`);
  }
  if (filters.status !== "all") {
    params.push(`status=${filters.status}`);
  }
  if (filters.status !== "open") {
    params.push(`from=${encodeURIComponent(filters.range.from.toISOString())}`);
    params.push(`to=${encodeURIComponent(filters.range.to.toISOString())}`);
  }
  params.push(`limit=${String(limit)}`);
  params.push(`offset=${String(offset)}`);
  return `/api/trades/?${params.join("&")}`;
}

function TradesPage(): React.JSX.Element {
  const navigate = useNavigate();
  const [filters, setFilters] = React.useState<Filters>(() => {
    const now = new Date();
    return {
      botId: "",
      symbol: "",
      status: "all",
      range: { from: new Date(now.getTime() - WINDOW_30D_MS), to: now, preset: "30d" },
    };
  });
  const [page, setPage] = React.useState(0);
  const offset = page * PAGE_SIZE;

  const tradesQuery = useQuery({
    queryKey: ["trades-list", filters, offset],
    queryFn: () =>
      apiFetch<TradeListResponse>(buildTradesUrl(filters, PAGE_SIZE, offset)),
  });

  const total = tradesQuery.data?.total ?? 0;
  const trades = tradesQuery.data?.trades ?? [];
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Filters</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <BotSelector
            value={filters.botId}
            onChange={(v) => {
              setFilters((f) => ({ ...f, botId: typeof v === "string" ? v : "" }));
              setPage(0);
            }}
            placeholder="All bots"
          />
          <Input
            placeholder="Symbol (e.g. BTCUSDT)"
            value={filters.symbol}
            onChange={(e) => {
              setFilters((f) => ({ ...f, symbol: e.target.value }));
              setPage(0);
            }}
            className="h-10 w-[200px]"
          />
          <select
            data-testid="status-filter"
            value={filters.status}
            onChange={(e) => {
              setFilters((f) => ({ ...f, status: e.target.value as StatusFilter }));
              setPage(0);
            }}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="all">All statuses</option>
            <option value="open">Open</option>
            <option value="closed">Closed</option>
            <option value="error">Error</option>
          </select>
          <div title="Filters by close time (closed_at column)">
            <TimeRangePicker
              value={filters.range}
              onChange={(range) => {
                setFilters((f) => ({ ...f, range }));
                setPage(0);
              }}
            />
          </div>
        </CardContent>
      </Card>

      {tradesQuery.isLoading ? (
        <div className="text-muted-foreground">Loading…</div>
      ) : tradesQuery.isError ? (
        <div className="text-red-400 text-sm">Failed to load trades</div>
      ) : (
        <>
          <DataTable
            columns={TRADE_COLUMNS}
            data={trades}
            pageSize={PAGE_SIZE}
            enableColumnVisibility={false}
            enableFiltering={false}
            enableSorting={false}
            onRowClick={(t) =>
              void navigate({ to: "/trades/$tradeId", params: { tradeId: String(t.id) } })
            }
            emptyMessage="No trades match filters"
          />

          <div
            data-testid="trades-pagination"
            className="flex items-center justify-between text-sm"
          >
            <span className="text-muted-foreground">
              Page {page + 1} of {totalPages} ({total} trades)
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => p + 1)}
                disabled={(page + 1) * PAGE_SIZE >= total}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

const TRADE_COLUMNS: ColumnDef<Trade, unknown>[] = [
  { accessorKey: "id", header: "#" },
  { accessorKey: "bot_id", header: "Bot" },
  { accessorKey: "symbol", header: "Symbol" },
  { accessorKey: "side", header: "Side" },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge kind="trade" status={row.original.status} />,
  },
  {
    accessorKey: "entry_price",
    header: "Entry",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.entry_price}</span>
    ),
  },
  {
    accessorKey: "exit_price",
    header: "Exit",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.exit_price ?? "—"}</span>
    ),
  },
  {
    accessorKey: "realized_pnl",
    header: "Realized P&L",
    cell: ({ row }) =>
      row.original.realized_pnl !== null ? (
        <PriceDelta value={row.original.realized_pnl} />
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    accessorKey: "close_reason",
    header: "Close reason",
    cell: ({ row }) => (
      <span className="text-xs text-muted-foreground">
        {row.original.close_reason ?? "—"}
      </span>
    ),
  },
];
