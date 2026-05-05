// T-415 — Section 4 Backtest detail. Single-page card per OQ-4=A.
// 404 handling via T-414 WG#2 echo (substring detect against apiFetch
// error format).

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api-client";
import type { BacktestRun } from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

export const Route = createFileRoute("/backtests/$runId")({
  component: BacktestDrillDown,
});

const STALE_MS = 30_000;

function BacktestDrillDown(): React.JSX.Element {
  const { runId } = Route.useParams();

  const runQuery = useQuery({
    queryKey: ["backtest", runId],
    queryFn: () => apiFetch<BacktestRun>(`/api/backtests/${runId}`),
    staleTime: STALE_MS,
    retry: false,
  });

  if (runQuery.isError) {
    const is404 = runQuery.error.message.includes("404");
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4">
        <div className="text-lg" data-testid="backtest-not-found">
          {is404 ? `Backtest #${runId} not found` : "Failed to load backtest"}
        </div>
        <Link to="/backtests" className="text-sm text-primary underline">
          Back to Backtests
        </Link>
      </div>
    );
  }

  const run = runQuery.data;

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between border-b border-border pb-3">
        <div className="flex items-center gap-3">
          <div className="text-lg font-semibold" data-testid="backtest-name">
            {run?.name ?? "Loading…"}
          </div>
          {run !== undefined && <StatusBadge kind="backtest" status={run.status} />}
        </div>
        <Link to="/backtests" className="text-sm text-primary underline">
          Back to Backtests
        </Link>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Run metadata</CardTitle>
        </CardHeader>
        <CardContent>
          {runQuery.isLoading ? (
            <span className="text-muted-foreground">Loading…</span>
          ) : run !== undefined ? (
            <dl
              data-testid="run-metadata"
              className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3"
            >
              <Row label="Run ID" value={<span className="font-mono text-xs">{run.id}</span>} />
              <Row label="Bot" value={run.bot_id} />
              <Row label="Config hash" value={<span className="font-mono text-xs">{run.config_hash.slice(0, 12)}…</span>} />
              <Row label="Range start" value={formatUtcDateTime(run.date_range_start)} />
              <Row label="Range end" value={formatUtcDateTime(run.date_range_end)} />
              <Row label="Started at" value={formatUtcDateTime(run.started_at)} />
              <Row
                label="Finished at"
                value={run.finished_at !== null ? formatUtcDateTime(run.finished_at) : "—"}
              />
            </dl>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Config YAML</CardTitle>
        </CardHeader>
        <CardContent>
          {run !== undefined ? (
            <details>
              <summary className="cursor-pointer text-xs text-muted-foreground">
                Show config_yaml ({String(run.config_yaml.length)} chars)
              </summary>
              <pre
                data-testid="config-yaml-pre"
                className="mt-2 max-h-96 overflow-auto rounded-md border border-border bg-muted/30 p-2 font-mono text-xs"
              >
                {run.config_yaml}
              </pre>
            </details>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Summary</CardTitle>
        </CardHeader>
        <CardContent>
          {run === undefined ? (
            <span className="text-muted-foreground">Loading…</span>
          ) : run.summary === null ? (
            <div data-testid="summary-pending" className="text-sm text-muted-foreground">
              F5+ worker pending — backtest summary will populate after compute
            </div>
          ) : (
            <pre
              data-testid="summary-json"
              className="max-h-96 overflow-auto rounded-md border border-border bg-muted/30 p-2 font-mono text-xs"
            >
              {JSON.stringify(run.summary, null, 2)}
            </pre>
          )}
        </CardContent>
      </Card>

      {run !== undefined && run.notes !== null && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Notes</CardTitle>
          </CardHeader>
          <CardContent>
            <div data-testid="run-notes" className="whitespace-pre-wrap text-sm">
              {run.notes}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Row({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
