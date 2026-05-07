// T-416 — Section 5 Strategy editor. Per BRIEF §14.3:2064. Plain
// `<textarea>` editor + 500ms-debounced live validation + side-by-side
// diff + Apply confirmation modal + inline versions panel. Reuses
// T-414 patterns (404 substring detect, custom DataTable rendering,
// formatUtcDateTime).

import { type ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { DataTable } from "@/components/DataTable";
import { YamlDiffView } from "@/components/YamlDiffView";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api-client";
import type {
  BotConfig,
  BotConfigVersionsListResponse,
  ConfigApplyRequest,
} from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";
import { useDebouncedValidation } from "@/lib/hooks/useDebouncedValidation";

export const Route = createFileRoute("/strategy/$botId")({
  component: StrategyEditorPage,
});

// Per WG#5 / L-001 — named constants with rationale comments. 30s
// matches global staleTime from main.tsx.
const STALE_MS = 30_000;
const VERSIONS_PAGE_SIZE = 50;

function StrategyEditorPage(): React.JSX.Element {
  const { botId } = Route.useParams();
  const queryClient = useQueryClient();

  const currentConfigQuery = useQuery({
    queryKey: ["config-current", botId],
    queryFn: () => apiFetch<BotConfig>(`/api/configs/${botId}`),
    staleTime: STALE_MS,
    retry: false,
  });

  const versionsQuery = useQuery({
    queryKey: ["config-versions", botId],
    queryFn: () =>
      apiFetch<BotConfigVersionsListResponse>(
        `/api/configs/${botId}/versions?limit=${String(VERSIONS_PAGE_SIZE)}`,
      ),
    staleTime: STALE_MS,
  });

  // Per WG#9 — 404 substring detect; fresh-bot is not an error.
  const isFreshBot =
    currentConfigQuery.isError && currentConfigQuery.error.message.includes("404");

  const [yamlText, setYamlText] = React.useState("");

  // Seed editor with current config on first load.
  React.useEffect(() => {
    if (currentConfigQuery.data !== undefined) {
      setYamlText(currentConfigQuery.data.config_yaml);
    }
  }, [currentConfigQuery.data]);

  const validation = useDebouncedValidation(yamlText, botId);

  const [applyOpen, setApplyOpen] = React.useState(false);
  const [appliedBy, setAppliedBy] = React.useState("");
  const [notes, setNotes] = React.useState("");

  const applyMutation = useMutation({
    mutationFn: (body: ConfigApplyRequest) =>
      apiFetch<BotConfig>(`/api/configs/${botId}/apply`, {
        method: "POST",
        body,
      }),
    onSuccess: () => {
      // Per WG#8 — invalidate BOTH query keys.
      void queryClient.invalidateQueries({ queryKey: ["config-current", botId] });
      void queryClient.invalidateQueries({ queryKey: ["config-versions", botId] });
      setApplyOpen(false);
      setAppliedBy("");
      setNotes("");
    },
  });

  const handleVersionRowClick = (row: BotConfig): void => {
    // Browser-native confirm chosen over shadcn <Dialog> for v1 —
    // minimal interruption + no extra modal state machine. F5+ may
    // swap if shadcn Dialog UX wins consistency review.
    if (
      yamlText !== currentConfigQuery.data?.config_yaml &&
      !window.confirm(
        `Discard unsaved changes and load v${String(row.version)}?`,
      )
    ) {
      return;
    }
    setYamlText(row.config_yaml);
  };

  const handleApplySubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    if (applyMutation.isPending) return;
    applyMutation.mutate({
      yaml_text: yamlText,
      applied_by: appliedBy,
      notes: notes === "" ? null : notes,
    });
  };

  // Per WG#7 — compound disable condition (3 guards).
  const applyDisabled =
    validation.isPending || !validation.valid || applyMutation.isPending;

  if (currentConfigQuery.isError && !isFreshBot) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4">
        <div className="text-lg" data-testid="strategy-load-error">
          Failed to load config for {botId}
        </div>
        <Link to="/" className="text-sm text-primary underline">
          Back to Overview
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between border-b border-border pb-3">
        <div className="text-lg font-semibold" data-testid="strategy-header">
          Strategy editor — Bot {botId}
        </div>
        <Link to="/" className="text-sm text-primary underline">
          Back to Overview
        </Link>
      </header>

      {isFreshBot && (
        <div
          data-testid="fresh-bot-message"
          className="rounded-md border border-dashed border-border bg-muted/20 p-3 text-sm text-muted-foreground"
        >
          No active config (this is a fresh bot — apply to create v1)
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Editor (new YAML)</CardTitle>
          </CardHeader>
          <CardContent>
            <textarea
              data-testid="yaml-editor"
              value={yamlText}
              onChange={(e) => setYamlText(e.target.value)}
              rows={20}
              className="min-h-[400px] w-full rounded-md border border-input bg-background p-2 font-mono text-xs"
              placeholder="bot_id: alpha&#10;exchange: { mode: paper }&#10;..."
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Diff vs current</CardTitle>
          </CardHeader>
          <CardContent>
            <YamlDiffView
              current={currentConfigQuery.data?.config_yaml ?? ""}
              draft={yamlText}
            />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Validation</CardTitle>
        </CardHeader>
        <CardContent>
          <ValidationPanel result={validation} />
        </CardContent>
      </Card>

      <div className="flex gap-2">
        <Button
          data-testid="apply-button"
          disabled={applyDisabled}
          onClick={() => setApplyOpen(true)}
        >
          Apply…
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Versions</CardTitle>
        </CardHeader>
        <CardContent>
          {versionsQuery.isLoading ? (
            <span className="text-muted-foreground">Loading…</span>
          ) : versionsQuery.isError ? (
            <span className="text-red-400 text-sm">Failed to load versions</span>
          ) : (
            <DataTable
              columns={VERSION_COLUMNS}
              data={versionsQuery.data?.versions ?? []}
              pageSize={VERSIONS_PAGE_SIZE}
              enableColumnVisibility={false}
              enableFiltering={false}
              enableSorting={false}
              onRowClick={handleVersionRowClick}
              emptyMessage="No versions yet"
            />
          )}
        </CardContent>
      </Card>

      <Dialog open={applyOpen} onOpenChange={setApplyOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Apply new config version</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleApplySubmit} className="space-y-3">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Applied by</span>
              <Input
                data-testid="applied-by-input"
                value={appliedBy}
                onChange={(e) => setAppliedBy(e.target.value)}
                required
                maxLength={128}
                placeholder="e.g. luster"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">Notes (optional)</span>
              <textarea
                data-testid="apply-notes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                maxLength={500}
                rows={2}
                className="rounded-md border border-input bg-background p-2 text-sm"
              />
            </label>
            {applyMutation.isError && (
              <div data-testid="apply-error" className="text-sm text-red-400">
                {applyMutation.error.message}
              </div>
            )}
            <div className="flex gap-2">
              <Button
                type="submit"
                data-testid="apply-confirm"
                disabled={applyMutation.isPending}
              >
                {applyMutation.isPending ? "Applying…" : "Confirm apply"}
              </Button>
              <Button type="button" variant="outline" onClick={() => setApplyOpen(false)}>
                Cancel
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface ValidationPanelProps {
  result: ReturnType<typeof useDebouncedValidation>;
}

function ValidationPanel({ result }: ValidationPanelProps): React.JSX.Element {
  if (result.isPending) {
    return (
      <span data-testid="validation-pending" className="text-sm text-muted-foreground">
        Validating…
      </span>
    );
  }
  if (result.valid) {
    return (
      <span data-testid="validation-valid" className="text-sm text-green-400">
        valid
        {result.parsedVersion !== null
          ? ` (parsed v${String(result.parsedVersion)})`
          : ""}
      </span>
    );
  }
  return (
    <ul data-testid="validation-errors" className="space-y-1 text-sm text-red-400">
      {result.errors.map((err, idx) => (
        <li key={`${String(idx)}-${err.slice(0, 24)}`} className="font-mono text-xs">
          {err}
        </li>
      ))}
    </ul>
  );
}

const VERSION_COLUMNS: ColumnDef<BotConfig, unknown>[] = [
  {
    accessorKey: "version",
    header: "v",
    cell: ({ row }) => <span className="font-mono">{row.original.version}</span>,
  },
  {
    accessorKey: "applied_at",
    header: "Applied at",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{formatUtcDateTime(row.original.applied_at)}</span>
    ),
  },
  { accessorKey: "applied_by", header: "Applied by" },
  {
    accessorKey: "notes",
    header: "Notes",
    cell: ({ row }) => (
      <span className="truncate text-xs text-muted-foreground">
        {row.original.notes !== null && row.original.notes.length > 50
          ? `${row.original.notes.slice(0, 50)}…`
          : (row.original.notes ?? "—")}
      </span>
    ),
  },
];
