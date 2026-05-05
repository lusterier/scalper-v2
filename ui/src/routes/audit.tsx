// T-419 — Section 8 Audit log viewer. Per BRIEF §14.3:2067. Single
// /audit route per OQ-1=A; 4 filters (actor + action_prefix +
// entity_type + TimeRangePicker) per OQ-2=A; ?correlation_id= URL
// search-param consumer per OQ-3=A; before_state / after_state JSON
// pretty-print per OQ-4=A. Inline row-expand drill-down (single-state
// expandedId; reset on filter/page change per WG#7).

import { type ColumnDef } from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { CorrelationIdChip } from "@/components/CorrelationIdChip";
import { DataTable } from "@/components/DataTable";
import { type TimeRange, TimeRangePicker } from "@/components/TimeRangePicker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api-client";
import type { AuditEvent, AuditEventListResponse } from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

export const Route = createFileRoute("/audit")({
  validateSearch: (search): { correlation_id?: string } => ({
    // Per WG#5 — empty string MUST coerce to undefined; otherwise
    // events.filter(e => e.correlation_id === "") would filter to none.
    correlation_id:
      typeof search["correlation_id"] === "string" &&
      search["correlation_id"].length > 0
        ? search["correlation_id"]
        : undefined,
  }),
  component: AuditLogPage,
});

// Per L-001 + WG#1 — PAGE_SIZE=50 mirrors backend `_DEFAULT_LIMIT`. NO
// STALE_MS constant: list view is filter-driven (mirror scoring.index
// + trades.index precedent — refetch fires only on filter/page change).
const PAGE_SIZE = 50;
const WINDOW_30D_MS = 30 * 24 * 60 * 60 * 1000;

interface Filters {
  actor: string;
  actionPrefix: string;
  entityType: string;
  range: TimeRange;
}

// Per WG#2 — `?correlation_id=` deliberately NOT appended to backend
// fetch URL: backend lacks correlation_id filter; param would be
// silently ignored (FastAPI default) — keep fetch URL deterministic
// for cache hit-rate. Client-side filter applied separately.
function buildAuditUrl(filters: Filters, limit: number, offset: number): string {
  const params: string[] = [];
  if (filters.actor !== "") {
    params.push(`actor=${encodeURIComponent(filters.actor)}`);
  }
  if (filters.actionPrefix !== "") {
    params.push(`action_prefix=${encodeURIComponent(filters.actionPrefix)}`);
  }
  if (filters.entityType !== "") {
    params.push(`entity_type=${encodeURIComponent(filters.entityType)}`);
  }
  params.push(`from=${encodeURIComponent(filters.range.from.toISOString())}`);
  params.push(`to=${encodeURIComponent(filters.range.to.toISOString())}`);
  params.push(`limit=${String(limit)}`);
  params.push(`offset=${String(offset)}`);
  return `/api/audit/?${params.join("&")}`;
}

function AuditLogPage(): React.JSX.Element {
  const { correlation_id: correlationFilter } = Route.useSearch();

  const [filters, setFilters] = React.useState<Filters>(() => {
    const now = new Date();
    return {
      actor: "",
      actionPrefix: "",
      entityType: "",
      range: { from: new Date(now.getTime() - WINDOW_30D_MS), to: now, preset: "30d" },
    };
  });
  const [page, setPage] = React.useState(0);
  const offset = page * PAGE_SIZE;
  const [expandedId, setExpandedId] = React.useState<number | null>(null);

  // Per WG#7 — reset expand state when filters / page change so stale
  // expandedId from prior page can't reference a row that no longer
  // exists in events array.
  React.useEffect(() => {
    setExpandedId(null);
  }, [filters, page]);

  const auditQuery = useQuery({
    queryKey: ["audit-list", filters, offset],
    queryFn: () =>
      apiFetch<AuditEventListResponse>(buildAuditUrl(filters, PAGE_SIZE, offset)),
  });

  const total = auditQuery.data?.total ?? 0;
  const allEvents = auditQuery.data?.events ?? [];
  const events =
    correlationFilter !== undefined
      ? allEvents.filter((e) => e.correlation_id === correlationFilter)
      : allEvents;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const expandedEvent = expandedId !== null ? events.find((e) => e.id === expandedId) : undefined;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Filters</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Input
            data-testid="actor-input"
            placeholder="Actor (e.g. lan:127.0.0.1)"
            value={filters.actor}
            onChange={(e) => {
              setFilters((f) => ({ ...f, actor: e.target.value }));
              setPage(0);
            }}
            className="h-10 w-[220px]"
          />
          <Input
            data-testid="action-prefix-input"
            placeholder="Action prefix (e.g. bot_config.)"
            value={filters.actionPrefix}
            onChange={(e) => {
              setFilters((f) => ({ ...f, actionPrefix: e.target.value }));
              setPage(0);
            }}
            className="h-10 w-[220px]"
          />
          <Input
            data-testid="entity-type-input"
            placeholder="Entity type (e.g. bot_config)"
            value={filters.entityType}
            onChange={(e) => {
              setFilters((f) => ({ ...f, entityType: e.target.value }));
              setPage(0);
            }}
            className="h-10 w-[200px]"
          />
          <TimeRangePicker
            value={filters.range}
            onChange={(range) => {
              setFilters((f) => ({ ...f, range }));
              setPage(0);
            }}
          />
        </CardContent>
      </Card>

      {correlationFilter !== undefined && (
        <div
          data-testid="correlation-filter-notice"
          className="flex items-center justify-between rounded-md border border-dashed border-border bg-muted/20 p-3 text-sm"
        >
          <span>
            Filtering by correlation_id=
            <span className="font-mono">{correlationFilter}</span> (client-side; only current page)
          </span>
          <Link
            to="/audit"
            search={{}}
            className="text-xs text-primary underline"
            data-testid="correlation-filter-clear"
          >
            Clear
          </Link>
        </div>
      )}

      {auditQuery.isLoading ? (
        <div className="text-muted-foreground">Loading…</div>
      ) : auditQuery.isError ? (
        <div className="text-red-400 text-sm">Failed to load audit events</div>
      ) : (
        <>
          <DataTable
            columns={EVENT_COLUMNS}
            data={events}
            pageSize={PAGE_SIZE}
            enableColumnVisibility={false}
            enableFiltering={false}
            enableSorting={false}
            onRowClick={(row) =>
              setExpandedId((prev) => (prev === row.id ? null : row.id))
            }
            emptyMessage="No audit events match filters"
          />

          <div
            data-testid="audit-pagination"
            className="flex items-center justify-between text-sm"
          >
            <span className="text-muted-foreground">
              Page {page + 1} of {totalPages} ({total} events)
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

      {expandedEvent !== undefined && (
        <Card data-testid="audit-expand-row">
          <CardHeader>
            <CardTitle className="flex items-center gap-3 text-sm">
              <span>Event #{String(expandedEvent.id)}</span>
              <span className="text-muted-foreground">·</span>
              <span className="font-mono">{expandedEvent.action}</span>
              <span className="ml-auto text-xs text-muted-foreground">
                {formatUtcDateTime(expandedEvent.occurred_at)}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <AuditExpandRow event={expandedEvent} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function AuditExpandRow({ event }: { event: AuditEvent }): React.JSX.Element {
  return (
    <div className="space-y-3">
      <StateBlock label="before_state" state={event.before_state} />
      <StateBlock label="after_state" state={event.after_state} />
      {Object.keys(event.meta).length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">
            meta ({String(Object.keys(event.meta).length)} keys)
          </summary>
          <pre
            data-testid="audit-meta-pre"
            className="mt-2 max-h-64 overflow-auto rounded-md border border-border bg-muted/20 p-2 font-mono text-xs"
          >
            {JSON.stringify(event.meta, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

function StateBlock({
  label,
  state,
}: {
  label: string;
  state: Record<string, unknown> | null;
}): React.JSX.Element {
  if (state === null) {
    return (
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">
          {label}: null
        </summary>
        <div data-testid={`${label}-null`} className="mt-1 text-xs text-muted-foreground">
          (no {label} —{" "}
          {label === "before_state" ? "first version" : "entity removed"})
        </div>
      </details>
    );
  }
  return (
    <details open>
      <summary className="cursor-pointer text-xs text-muted-foreground">
        {label} ({String(Object.keys(state).length)} keys)
      </summary>
      <pre
        data-testid={`${label}-pre`}
        className="mt-2 max-h-64 overflow-auto rounded-md border border-border bg-muted/20 p-2 font-mono text-xs"
      >
        {JSON.stringify(state, null, 2)}
      </pre>
    </details>
  );
}

const EVENT_COLUMNS: ColumnDef<AuditEvent, unknown>[] = [
  {
    accessorKey: "occurred_at",
    header: "Occurred at",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{formatUtcDateTime(row.original.occurred_at)}</span>
    ),
  },
  { accessorKey: "actor", header: "Actor" },
  { accessorKey: "action", header: "Action" },
  { accessorKey: "entity_type", header: "Entity type" },
  { accessorKey: "entity_id", header: "Entity ID" },
  {
    accessorKey: "correlation_id",
    header: "Correlation",
    cell: ({ row }) =>
      row.original.correlation_id !== null ? (
        <CorrelationIdChip correlationId={row.original.correlation_id} />
      ) : (
        <span className="text-xs text-muted-foreground">—</span>
      ),
  },
];
