// T-415 — Section 4 Backtest lab list + new-run form. Per BRIEF
// §14.3:2063. Reuses T-414 patterns: filters + custom pagination +
// click-row-to-drill-down.

import { type ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { BotSelector } from "@/components/BotSelector";
import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { type TimeRange, TimeRangePicker } from "@/components/TimeRangePicker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api-client";
import type {
  BacktestRun,
  BacktestRunCreateRequest,
  BacktestRunListResponse,
} from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

export const Route = createFileRoute("/backtests/")({
  component: BacktestsPage,
});

// Per WG#6 — named constants with rationale comments per L-001 + T-414
// WG#5 echo. PAGE_SIZE=50 mirrors backend `_DEFAULT_LIMIT` in
// services/analytics_api/app/routers/backtests.py:_DEFAULT_LIMIT.
const PAGE_SIZE = 50;
const WINDOW_30D_MS = 30 * 24 * 60 * 60 * 1000;

type StatusFilter = "all" | "queued" | "running" | "completed" | "failed";

interface Filters {
  botId: string;
  status: StatusFilter;
  range: TimeRange;
}

// Per WG#7 — status="all" omits ?status= entirely. Test 3 asserts
// negative URL regex. Mirrors T-414 buildTradesUrl pattern (without
// the closed_at branch — backtests filter started_at, valid for all
// statuses).
function buildBacktestsUrl(filters: Filters, limit: number, offset: number): string {
  const params: string[] = [];
  if (filters.botId !== "") {
    params.push(`bot_id=${encodeURIComponent(filters.botId)}`);
  }
  if (filters.status !== "all") {
    params.push(`status=${filters.status}`);
  }
  params.push(`from=${encodeURIComponent(filters.range.from.toISOString())}`);
  params.push(`to=${encodeURIComponent(filters.range.to.toISOString())}`);
  params.push(`limit=${String(limit)}`);
  params.push(`offset=${String(offset)}`);
  return `/api/backtests/?${params.join("&")}`;
}

function BacktestsPage(): React.JSX.Element {
  const navigate = useNavigate();
  const [filters, setFilters] = React.useState<Filters>(() => {
    const now = new Date();
    return {
      botId: "",
      status: "all",
      range: { from: new Date(now.getTime() - WINDOW_30D_MS), to: now, preset: "30d" },
    };
  });
  const [page, setPage] = React.useState(0);
  const offset = page * PAGE_SIZE;

  const runsQuery = useQuery({
    queryKey: ["backtests-list", filters, offset],
    queryFn: () =>
      apiFetch<BacktestRunListResponse>(buildBacktestsUrl(filters, PAGE_SIZE, offset)),
  });

  const total = runsQuery.data?.total ?? 0;
  const runs = runsQuery.data?.runs ?? [];
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
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
          <div title="Filters by started_at">
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

      <NewRunFormCard />

      {runsQuery.isLoading ? (
        <div className="text-muted-foreground">Loading…</div>
      ) : runsQuery.isError ? (
        <div className="text-red-400 text-sm">Failed to load backtests</div>
      ) : (
        <>
          <DataTable
            columns={RUN_COLUMNS}
            data={runs}
            pageSize={PAGE_SIZE}
            enableColumnVisibility={false}
            enableFiltering={false}
            enableSorting={false}
            onRowClick={(r) =>
              void navigate({ to: "/backtests/$runId", params: { runId: r.id } })
            }
            emptyMessage="No backtests match filters"
          />

          <div
            data-testid="backtests-pagination"
            className="flex items-center justify-between text-sm"
          >
            <span className="text-muted-foreground">
              Page {page + 1} of {totalPages} ({total} runs)
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

const RUN_COLUMNS: ColumnDef<BacktestRun, unknown>[] = [
  {
    accessorKey: "id",
    header: "Run ID",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.id.slice(0, 8)}…</span>
    ),
  },
  { accessorKey: "name", header: "Name" },
  { accessorKey: "bot_id", header: "Bot" },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge kind="backtest" status={row.original.status} />,
  },
  {
    accessorKey: "date_range_start",
    header: "Range start",
    cell: ({ row }) => (
      <span className="font-mono text-xs">
        {formatUtcDateTime(row.original.date_range_start)}
      </span>
    ),
  },
  {
    accessorKey: "date_range_end",
    header: "Range end",
    cell: ({ row }) => (
      <span className="font-mono text-xs">
        {formatUtcDateTime(row.original.date_range_end)}
      </span>
    ),
  },
  {
    accessorKey: "started_at",
    header: "Started",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{formatUtcDateTime(row.original.started_at)}</span>
    ),
  },
  {
    accessorKey: "finished_at",
    header: "Finished",
    cell: ({ row }) => (
      <span className="font-mono text-xs">
        {row.original.finished_at !== null
          ? formatUtcDateTime(row.original.finished_at)
          : "—"}
      </span>
    ),
  },
];

function NewRunFormCard(): React.JSX.Element {
  const [expanded, setExpanded] = React.useState(false);
  const [name, setName] = React.useState("");
  const [botId, setBotId] = React.useState("");
  const [configYaml, setConfigYaml] = React.useState("");
  const [dateStart, setDateStart] = React.useState("");
  const [dateEnd, setDateEnd] = React.useState("");
  const [notes, setNotes] = React.useState("");

  const queryClient = useQueryClient();

  const createMutation = useMutation({
    mutationFn: (body: BacktestRunCreateRequest) =>
      apiFetch<BacktestRun>("/api/backtests/", { method: "POST", body }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["backtests-list"] });
      setName("");
      setConfigYaml("");
      setDateStart("");
      setDateEnd("");
      setNotes("");
      setExpanded(false);
    },
  });

  const handleSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    if (createMutation.isPending) return;
    // Per WG#3 — `<input type="datetime-local">` value is naive
    // browser-local string ("yyyy-MM-ddTHH:mm"). `new Date(value)`
    // interprets it in browser-local TZ; `.toISOString()` then converts
    // to UTC Z-suffix per §N1. If operator's machine is CEST (UTC+2),
    // input "12:00" becomes "10:00:00Z" in body — correct, not a bug.
    const body: BacktestRunCreateRequest = {
      name,
      bot_id: botId,
      config_yaml: configYaml,
      date_range_start: new Date(dateStart).toISOString(),
      date_range_end: new Date(dateEnd).toISOString(),
      notes: notes === "" ? null : notes,
    };
    createMutation.mutate(body);
  };

  return (
    <Card data-testid="new-run-form-card">
      <CardHeader
        className="cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
        data-testid="new-run-toggle"
      >
        <CardTitle className="text-sm">
          {expanded ? "− Cancel new backtest" : "+ New backtest run"}
        </CardTitle>
      </CardHeader>
      {expanded && (
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Name</span>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  maxLength={200}
                  placeholder="e.g. baseline-2026-Q2"
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Bot</span>
                <BotSelector
                  value={botId}
                  onChange={(v) => setBotId(typeof v === "string" ? v : "")}
                  placeholder="Pick bot"
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Date range start</span>
                <Input
                  type="datetime-local"
                  value={dateStart}
                  onChange={(e) => setDateStart(e.target.value)}
                  required
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">Date range end</span>
                <Input
                  type="datetime-local"
                  value={dateEnd}
                  onChange={(e) => setDateEnd(e.target.value)}
                  required
                />
              </label>
            </div>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Config YAML</span>
              <textarea
                data-testid="config-yaml-textarea"
                value={configYaml}
                onChange={(e) => setConfigYaml(e.target.value)}
                required
                maxLength={200_000}
                rows={10}
                className="min-h-[180px] rounded-md border border-input bg-background p-2 font-mono text-xs"
                placeholder="bot_id: alpha&#10;exchange: { mode: paper }&#10;..."
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Notes (optional)</span>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                maxLength={1000}
                rows={2}
                className="rounded-md border border-input bg-background p-2 text-sm"
              />
            </label>
            {createMutation.isError && (
              <div data-testid="new-run-error" className="text-sm text-red-400">
                {createMutation.error.message}
              </div>
            )}
            <div className="flex gap-2">
              <Button
                type="submit"
                data-testid="new-run-submit"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? "Submitting…" : "Submit"}
              </Button>
              <Button type="button" variant="outline" onClick={() => setExpanded(false)}>
                Cancel
              </Button>
            </div>
          </form>
        </CardContent>
      )}
    </Card>
  );
}
