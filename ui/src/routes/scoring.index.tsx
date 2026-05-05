// T-418 — Section 7 Scoring inspector list. Per BRIEF §14.3:2066.
// Paginated + filtered signal list; click row → drill-down at
// /scoring/$signalId. 5 filters (source + symbol + action + ingestion_status
// + TimeRangePicker) per OQ-2=A.

import { type ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { type TimeRange, TimeRangePicker } from "@/components/TimeRangePicker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api-client";
import type { PaginatedSignalListResponse, Signal } from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

export const Route = createFileRoute("/scoring/")({
  component: ScoringInspectorPage,
});

// Per L-001 + WG#7 echo — named constants. PAGE_SIZE=50 mirrors
// backend `_DEFAULT_LIMIT` per services/analytics_api/app/routers/
// signals.py.
const PAGE_SIZE = 50;
const WINDOW_30D_MS = 30 * 24 * 60 * 60 * 1000;

// Per WG#3 — Action StrEnum values from packages/core/types.py:42-45;
// IngestionStatus values from :116-118.
type ActionFilter = "all" | "LONG" | "SHORT" | "CLOSE" | "CUSTOM";
type IngestionStatusFilter = "all" | "validated" | "duplicate" | "invalid";

interface Filters {
  source: string;
  symbol: string;
  action: ActionFilter;
  ingestionStatus: IngestionStatusFilter;
  range: TimeRange;
}

function buildSignalsUrl(filters: Filters, limit: number, offset: number): string {
  const params: string[] = [];
  if (filters.source !== "") {
    params.push(`source=${encodeURIComponent(filters.source)}`);
  }
  if (filters.symbol !== "") {
    params.push(`symbol=${encodeURIComponent(filters.symbol)}`);
  }
  // Per WG#4 — action="all" + ingestion_status="all" omit URL params.
  if (filters.action !== "all") {
    params.push(`action=${filters.action}`);
  }
  if (filters.ingestionStatus !== "all") {
    params.push(`ingestion_status=${filters.ingestionStatus}`);
  }
  params.push(`from=${encodeURIComponent(filters.range.from.toISOString())}`);
  params.push(`to=${encodeURIComponent(filters.range.to.toISOString())}`);
  params.push(`limit=${String(limit)}`);
  params.push(`offset=${String(offset)}`);
  return `/api/signals/?${params.join("&")}`;
}

function ScoringInspectorPage(): React.JSX.Element {
  const navigate = useNavigate();
  const [filters, setFilters] = React.useState<Filters>(() => {
    const now = new Date();
    return {
      source: "",
      symbol: "",
      action: "all",
      ingestionStatus: "all",
      range: { from: new Date(now.getTime() - WINDOW_30D_MS), to: now, preset: "30d" },
    };
  });
  const [page, setPage] = React.useState(0);
  const offset = page * PAGE_SIZE;

  const signalsQuery = useQuery({
    queryKey: ["signals-list", filters, offset],
    queryFn: () =>
      apiFetch<PaginatedSignalListResponse>(buildSignalsUrl(filters, PAGE_SIZE, offset)),
  });

  const total = signalsQuery.data?.total ?? 0;
  const signals = (signalsQuery.data?.signals ?? []) as Signal[];
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Filters</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Input
            data-testid="source-input"
            placeholder="Source (e.g. tv)"
            value={filters.source}
            onChange={(e) => {
              setFilters((f) => ({ ...f, source: e.target.value }));
              setPage(0);
            }}
            className="h-10 w-[160px]"
          />
          <Input
            data-testid="symbol-input"
            placeholder="Symbol (e.g. BTCUSDT)"
            value={filters.symbol}
            onChange={(e) => {
              setFilters((f) => ({ ...f, symbol: e.target.value }));
              setPage(0);
            }}
            className="h-10 w-[200px]"
          />
          <select
            data-testid="action-filter"
            value={filters.action}
            onChange={(e) => {
              setFilters((f) => ({ ...f, action: e.target.value as ActionFilter }));
              setPage(0);
            }}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="all">All actions</option>
            <option value="LONG">LONG</option>
            <option value="SHORT">SHORT</option>
            <option value="CLOSE">CLOSE</option>
            <option value="CUSTOM">CUSTOM</option>
          </select>
          <select
            data-testid="ingestion-status-filter"
            value={filters.ingestionStatus}
            onChange={(e) => {
              setFilters((f) => ({
                ...f,
                ingestionStatus: e.target.value as IngestionStatusFilter,
              }));
              setPage(0);
            }}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="all">All statuses</option>
            <option value="validated">Validated</option>
            <option value="duplicate">Duplicate</option>
            <option value="invalid">Invalid</option>
          </select>
          <TimeRangePicker
            value={filters.range}
            onChange={(range) => {
              setFilters((f) => ({ ...f, range }));
              setPage(0);
            }}
          />
        </CardContent>
      </Card>

      {signalsQuery.isLoading ? (
        <div className="text-muted-foreground">Loading…</div>
      ) : signalsQuery.isError ? (
        <div className="text-red-400 text-sm">Failed to load signals</div>
      ) : (
        <>
          <DataTable
            columns={SIGNAL_COLUMNS}
            data={signals}
            pageSize={PAGE_SIZE}
            enableColumnVisibility={false}
            enableFiltering={false}
            enableSorting={false}
            onRowClick={(s) =>
              void navigate({
                to: "/scoring/$signalId",
                params: { signalId: String(s.id) },
              })
            }
            emptyMessage="No signals match filters"
          />

          <div
            data-testid="scoring-pagination"
            className="flex items-center justify-between text-sm"
          >
            <span className="text-muted-foreground">
              Page {page + 1} of {totalPages} ({total} signals)
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

const SIGNAL_COLUMNS: ColumnDef<Signal, unknown>[] = [
  { accessorKey: "id", header: "#" },
  {
    accessorKey: "received_at",
    header: "Received at",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{formatUtcDateTime(row.original.received_at)}</span>
    ),
  },
  { accessorKey: "symbol", header: "Symbol" },
  { accessorKey: "action", header: "Action" },
  {
    accessorKey: "ingestion_status",
    header: "Status",
    cell: ({ row }) => (
      <StatusBadge kind="signal" status={row.original.ingestion_status} />
    ),
  },
];
