// T-517b2 — per-rejected-signal explorer list (BRIEF §13.6 third bullet
// "what would rejected signals have yielded?"). Mirror paper-trades.index.tsx
// modulo URL/type swaps + envelope key (`rejected` not `paper_trades`) +
// NEW terminal_outcome filter dropdown + NO row-navigate (OQ-1=A list-only;
// drill-down deferred). Per OQ-3=A 6-option terminal_outcome select; per
// OQ-4=A inline status badge (Pill-style; status values "active"/"terminated"
// are not in StatusBadge enum).
//
// Time range filter ALWAYS applies (NO omit-when-active heuristic; differs
// from paper-trades): rejected schema's created_at column is non-null per
// migration 0014, so backend `created_at >= from AND < to` predicates always
// yield meaningful sets — even for status="active" rows.

import { type ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { BotSelector } from "@/components/BotSelector";
import { DataTable } from "@/components/DataTable";
import { type TimeRange, TimeRangePicker } from "@/components/TimeRangePicker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api-client";
import type {
  ShadowRejected,
  ShadowRejectedListResponse,
  ShadowRejectedTerminal,
} from "@/lib/api-types";

export const Route = createFileRoute("/shadow/rejected")({
  component: ShadowRejectedPage,
});

// 50 = backend default per services/analytics_api/app/routers/shadow_rejected.py:_DEFAULT_LIMIT.
const PAGE_SIZE = 50;
const WINDOW_30D_MS = 30 * 24 * 60 * 60 * 1000;

type StatusFilter = "all" | "active" | "terminated";
type TerminalOutcomeFilter = "all" | ShadowRejectedTerminal;

// Per WG#6 — explicit enumeration; ShadowRejectedTerminal is a TS type
// alias (compile-time only), NOT a runtime enum, so Object.values() is not
// available. List MUST mirror packages/core/types.py:102 ShadowRejectedTerminal
// 5 values + "all" sentinel.
const TERMINAL_OUTCOME_OPTIONS: ReadonlyArray<{
  value: TerminalOutcomeFilter;
  label: string;
}> = [
  { value: "all", label: "All outcomes" },
  { value: "would_tp", label: "would_tp" },
  { value: "would_sl", label: "would_sl" },
  { value: "would_be", label: "would_be" },
  { value: "no_trigger", label: "no_trigger" },
  { value: "shutdown_mid_replay", label: "shutdown_mid_replay" },
];

interface Filters {
  botId: string;
  symbol: string;
  status: StatusFilter;
  terminalOutcome: TerminalOutcomeFilter;
  range: TimeRange;
}

function buildShadowRejectedUrl(filters: Filters, limit: number, offset: number): string {
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
  if (filters.terminalOutcome !== "all") {
    params.push(`terminal_outcome=${filters.terminalOutcome}`);
  }
  // WG#4 — NO omit-when-active heuristic (inversion of paper-trades.index
  // pattern): backend filters created_at column which is NON-NULL for every
  // shadow_rejected row per migration 0014. Time range thus produces
  // meaningful sets regardless of status value (active or terminated).
  // Paper-trades omits ?from/?to when status=open because closed_at IS NULL
  // for open rows; rejected has no analog to that null-on-open semantic.
  params.push(`from=${encodeURIComponent(filters.range.from.toISOString())}`);
  params.push(`to=${encodeURIComponent(filters.range.to.toISOString())}`);
  params.push(`limit=${String(limit)}`);
  params.push(`offset=${String(offset)}`);
  return `/api/shadow/rejected/?${params.join("&")}`;
}

function ShadowRejectedPage(): React.JSX.Element {
  const [filters, setFilters] = React.useState<Filters>(() => {
    const now = new Date();
    return {
      botId: "",
      symbol: "",
      status: "all",
      terminalOutcome: "all",
      range: { from: new Date(now.getTime() - WINDOW_30D_MS), to: now, preset: "30d" },
    };
  });
  const [page, setPage] = React.useState(0);
  const offset = page * PAGE_SIZE;

  const rejectedQuery = useQuery({
    queryKey: ["shadow-rejected-list", filters, offset],
    queryFn: () =>
      apiFetch<ShadowRejectedListResponse>(buildShadowRejectedUrl(filters, PAGE_SIZE, offset)),
  });

  const total = rejectedQuery.data?.total ?? 0;
  const rejected = rejectedQuery.data?.rejected ?? [];
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
            <option value="active">Active</option>
            <option value="terminated">Terminated</option>
          </select>
          <select
            data-testid="terminal-outcome-filter"
            value={filters.terminalOutcome}
            onChange={(e) => {
              setFilters((f) => ({
                ...f,
                terminalOutcome: e.target.value as TerminalOutcomeFilter,
              }));
              setPage(0);
            }}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
          >
            {TERMINAL_OUTCOME_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <div title="Filters by created_at (observation start time)">
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

      {rejectedQuery.isLoading ? (
        <div className="text-muted-foreground">Loading…</div>
      ) : rejectedQuery.isError ? (
        <div className="text-red-400 text-sm">Failed to load rejected signals</div>
      ) : (
        <>
          <DataTable
            columns={REJECTED_COLUMNS}
            data={rejected}
            pageSize={PAGE_SIZE}
            enableColumnVisibility={false}
            enableFiltering={false}
            enableSorting={false}
            emptyMessage="No rejected signals match filters"
          />

          <div
            data-testid="shadow-rejected-pagination"
            className="flex items-center justify-between text-sm"
          >
            <span className="text-muted-foreground">
              Page {page + 1} of {totalPages} ({total} rejected signals)
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

// WG#5 — Pill-style inline span; "active" / "terminated" status values are
// not in the existing StatusBadge enum, and the visual sits next to the
// outcome cell so a local Pill matches ShadowVariantsView.tsx convention.
function StatusPill({ terminated }: { terminated: boolean }): React.JSX.Element {
  return (
    <span
      className={
        "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium " +
        (terminated
          ? "bg-muted text-foreground"
          : "bg-yellow-500/15 text-yellow-400")
      }
    >
      {terminated ? "terminated" : "active"}
    </span>
  );
}

// WG#1 — plain text percent rendering (mirror ShadowVariantsView.formatPctPair
// at ui/src/components/ShadowVariantsView.tsx:194-197); MFE/MAE are statistical
// ratios, NOT money/price, so PriceDelta's sign-color price treatment is not
// applied. Sign-coloured Tailwind class is added based on numeric sign for
// quick visual scan.
function formatPct(n: number | null): React.JSX.Element {
  if (n === null) {
    return <span className="text-muted-foreground">—</span>;
  }
  const colour = n >= 0 ? "text-green-400" : "text-red-400";
  return <span className={`font-mono text-xs ${colour}`}>{`${(n * 100).toFixed(2)}%`}</span>;
}

const REJECTED_COLUMNS: ColumnDef<ShadowRejected, unknown>[] = [
  { accessorKey: "id", header: "#" },
  { accessorKey: "bot_id", header: "Bot" },
  { accessorKey: "symbol", header: "Symbol" },
  { accessorKey: "would_side", header: "Would side" },
  {
    accessorKey: "terminated_at",
    header: "Status",
    cell: ({ row }) => <StatusPill terminated={row.original.terminated_at !== null} />,
  },
  {
    accessorKey: "terminal_outcome",
    header: "Outcome",
    cell: ({ row }) =>
      row.original.terminal_outcome !== null ? (
        <span className="font-mono text-xs">{row.original.terminal_outcome}</span>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    accessorKey: "mfe_pct",
    header: "MFE %",
    cell: ({ row }) => formatPct(row.original.mfe_pct),
  },
  {
    accessorKey: "mae_pct",
    header: "MAE %",
    cell: ({ row }) => formatPct(row.original.mae_pct),
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => (
      <span className="text-xs text-muted-foreground">{row.original.created_at}</span>
    ),
  },
];
