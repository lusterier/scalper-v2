// T-420 — Section 9 Settings (LAST F4 dashboard section). Per BRIEF
// §14.3:2068. Single /settings route per OQ-1=A; 4 vertical sections.
// Symbol map CRUD modal-based per OQ-2=C; plugin registry + API key
// status placeholders per OQ-3=A / OQ-4=A. DELETE confirmation via
// window.confirm per OQ-5=A.

import { type ColumnDef } from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
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
  Bot,
  BotListResponse,
  SymbolMapEntry,
  SymbolMapEntryCreateRequest,
  SymbolMapEntryUpdateRequest,
  SymbolMapListResponse,
} from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

// Per WG#10 — literal-typed via `as const` so TypeScript
// noUncheckedIndexedAccess catches typos. Matches packages/core/types.py
// ExchangeSource StrEnum exactly.
const EXCHANGE_SOURCES = ["binance", "bybit", "custom"] as const;
type ExchangeSource = (typeof EXCHANGE_SOURCES)[number];

function SettingsPage(): React.JSX.Element {
  return (
    <div className="space-y-4">
      <BotRegistrySection />
      <SymbolMapSection />
      <PluginRegistryPlaceholder />
      <ApiKeyStatusPlaceholder />
    </div>
  );
}

function BotRegistrySection(): React.JSX.Element {
  const botsQuery = useQuery({
    queryKey: ["bots-list"],
    queryFn: () => apiFetch<BotListResponse>("/api/bots/"),
  });

  return (
    <Card data-testid="bot-registry-section">
      <CardHeader>
        <CardTitle className="text-sm">Bot registry</CardTitle>
      </CardHeader>
      <CardContent>
        {botsQuery.isLoading ? (
          <span className="text-muted-foreground">Loading…</span>
        ) : botsQuery.isError ? (
          <span className="text-red-400 text-sm">Failed to load bots</span>
        ) : (
          <DataTable
            columns={BOT_COLUMNS}
            data={botsQuery.data?.bots ?? []}
            enableColumnVisibility={false}
            enableFiltering={false}
            enableSorting={false}
            emptyMessage="No bots configured"
          />
        )}
      </CardContent>
    </Card>
  );
}

const BOT_COLUMNS: ColumnDef<Bot, unknown>[] = [
  { accessorKey: "bot_id", header: "Bot ID" },
  { accessorKey: "display_name", header: "Display name" },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge kind="bot" status={row.original.status} />,
  },
  { accessorKey: "exchange_mode", header: "Exchange mode" },
  {
    accessorKey: "config_hash",
    header: "Config hash",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.config_hash.slice(0, 12)}…</span>
    ),
  },
  {
    accessorKey: "config_applied_at",
    header: "Applied at",
    cell: ({ row }) => (
      <span className="font-mono text-xs">
        {formatUtcDateTime(row.original.config_applied_at)}
      </span>
    ),
  },
];

function SymbolMapSection(): React.JSX.Element {
  const queryClient = useQueryClient();
  const symbolMapQuery = useQuery({
    queryKey: ["symbol-map-list"],
    queryFn: () => apiFetch<SymbolMapListResponse>("/api/symbol-map/"),
  });

  const [dialogState, setDialogState] = React.useState<
    | { open: false }
    | { open: true; mode: "create"; initialEntry?: undefined }
    | { open: true; mode: "edit"; initialEntry: SymbolMapEntry }
  >({ open: false });

  // Per WG#7 — single string-array key matches useQuery key exactly.
  const invalidateList = (): void => {
    void queryClient.invalidateQueries({ queryKey: ["symbol-map-list"] });
  };

  const deleteMutation = useMutation({
    mutationFn: (input_symbol: string) =>
      apiFetch<void>(`/api/symbol-map/${encodeURIComponent(input_symbol)}`, {
        method: "DELETE",
      }),
    onSuccess: invalidateList,
  });

  const handleDelete = (entry: SymbolMapEntry): void => {
    // Per WG#4 + OQ-5=A — destructive admin action confirmation.
    if (!window.confirm(`Delete symbol_map entry ${entry.input_symbol}?`)) {
      return;
    }
    if (deleteMutation.isPending) return;
    deleteMutation.mutate(entry.input_symbol);
  };

  const SYMBOL_MAP_COLUMNS: ColumnDef<SymbolMapEntry, unknown>[] = [
    { accessorKey: "input_symbol", header: "Input symbol" },
    { accessorKey: "canonical_symbol", header: "Canonical symbol" },
    { accessorKey: "exchange_source", header: "Source" },
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
    {
      accessorKey: "updated_at",
      header: "Updated",
      cell: ({ row }) => (
        <span className="font-mono text-xs">
          {formatUtcDateTime(row.original.updated_at)}
        </span>
      ),
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            data-testid={`edit-${row.original.input_symbol}`}
            onClick={(e) => {
              e.stopPropagation();
              setDialogState({
                open: true,
                mode: "edit",
                initialEntry: row.original,
              });
            }}
          >
            Edit
          </Button>
          <Button
            variant="outline"
            size="sm"
            data-testid={`delete-${row.original.input_symbol}`}
            disabled={deleteMutation.isPending}
            onClick={(e) => {
              e.stopPropagation();
              handleDelete(row.original);
            }}
          >
            {deleteMutation.isPending ? "Deleting…" : "Delete"}
          </Button>
        </div>
      ),
    },
  ];

  return (
    <Card data-testid="symbol-map-section">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-sm">Symbol map</CardTitle>
        <Button
          size="sm"
          data-testid="add-symbol-map-entry"
          onClick={() => setDialogState({ open: true, mode: "create" })}
        >
          + Add entry
        </Button>
      </CardHeader>
      <CardContent>
        {symbolMapQuery.isLoading ? (
          <span className="text-muted-foreground">Loading…</span>
        ) : symbolMapQuery.isError ? (
          <span className="text-red-400 text-sm">Failed to load symbol map</span>
        ) : (
          <DataTable
            columns={SYMBOL_MAP_COLUMNS}
            data={symbolMapQuery.data?.entries ?? []}
            enableColumnVisibility={false}
            enableFiltering={false}
            enableSorting={false}
            emptyMessage="No symbol_map entries"
          />
        )}
      </CardContent>
      <SymbolMapEntryFormDialog
        state={dialogState}
        onClose={() => setDialogState({ open: false })}
        onSuccess={invalidateList}
      />
    </Card>
  );
}

interface FormDialogProps {
  state:
    | { open: false }
    | { open: true; mode: "create"; initialEntry?: undefined }
    | { open: true; mode: "edit"; initialEntry: SymbolMapEntry };
  onClose: () => void;
  onSuccess: () => void;
}

function SymbolMapEntryFormDialog({
  state,
  onClose,
  onSuccess,
}: FormDialogProps): React.JSX.Element {
  const isOpen = state.open;
  const mode = isOpen ? state.mode : "create";
  const initialEntry = isOpen && state.mode === "edit" ? state.initialEntry : undefined;

  const [inputSymbol, setInputSymbol] = React.useState("");
  const [canonicalSymbol, setCanonicalSymbol] = React.useState("");
  const [exchangeSource, setExchangeSource] = React.useState<ExchangeSource>("binance");
  const [notes, setNotes] = React.useState("");

  // Reset form when dialog opens with new initialEntry.
  React.useEffect(() => {
    if (!isOpen) return;
    if (initialEntry !== undefined) {
      setInputSymbol(initialEntry.input_symbol);
      setCanonicalSymbol(initialEntry.canonical_symbol);
      setExchangeSource(initialEntry.exchange_source);
      setNotes(initialEntry.notes ?? "");
    } else {
      setInputSymbol("");
      setCanonicalSymbol("");
      setExchangeSource("binance");
      setNotes("");
    }
  }, [isOpen, initialEntry]);

  const queryClient = useQueryClient();

  const createMutation = useMutation({
    mutationFn: (body: SymbolMapEntryCreateRequest) =>
      apiFetch<SymbolMapEntry>("/api/symbol-map/", { method: "POST", body }),
    onSuccess: () => {
      // Per WG#7 — invalidate the same key as useQuery in SymbolMapSection.
      void queryClient.invalidateQueries({ queryKey: ["symbol-map-list"] });
      onSuccess();
      onClose();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      input_symbol,
      body,
    }: {
      input_symbol: string;
      body: SymbolMapEntryUpdateRequest;
    }) =>
      apiFetch<SymbolMapEntry>(`/api/symbol-map/${encodeURIComponent(input_symbol)}`, {
        method: "PUT",
        body,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["symbol-map-list"] });
      onSuccess();
      onClose();
    },
  });

  const isPending = createMutation.isPending || updateMutation.isPending;
  const errorMessage =
    createMutation.error?.message ?? updateMutation.error?.message ?? null;

  const handleSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    // Per WG#1 — early-return guards double-submit even if button gets clicked.
    if (isPending) return;
    if (mode === "create") {
      createMutation.mutate({
        input_symbol: inputSymbol,
        canonical_symbol: canonicalSymbol,
        exchange_source: exchangeSource,
        notes: notes === "" ? null : notes,
      });
    } else {
      // Per WG#3 — PUT body excludes input_symbol; URL path is the PK.
      updateMutation.mutate({
        input_symbol: inputSymbol,
        body: {
          canonical_symbol: canonicalSymbol,
          exchange_source: exchangeSource,
          notes: notes === "" ? null : notes,
        },
      });
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            {mode === "create" ? "Add symbol_map entry" : `Edit symbol_map: ${inputSymbol}`}
          </DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-3">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">Input symbol</span>
            <Input
              data-testid="form-input-symbol"
              value={inputSymbol}
              onChange={(e) => setInputSymbol(e.target.value)}
              required
              maxLength={64}
              placeholder="e.g. BTCUSDT.P"
              // Per WG#3 — disabled in edit mode (PUT URL path is the PK).
              disabled={mode === "edit"}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">Canonical symbol</span>
            <Input
              data-testid="form-canonical-symbol"
              value={canonicalSymbol}
              onChange={(e) => setCanonicalSymbol(e.target.value)}
              required
              maxLength={64}
              placeholder="e.g. BTCUSDT"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">Exchange source</span>
            <select
              data-testid="form-exchange-source"
              value={exchangeSource}
              onChange={(e) => setExchangeSource(e.target.value as ExchangeSource)}
              className="h-10 rounded-md border border-input bg-background px-3 text-sm"
            >
              {EXCHANGE_SOURCES.map((source) => (
                <option key={source} value={source}>
                  {source}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">Notes (optional)</span>
            <textarea
              data-testid="form-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={500}
              rows={2}
              className="rounded-md border border-input bg-background p-2 text-sm"
            />
          </label>
          {errorMessage !== null && (
            <div data-testid="form-error" className="text-sm text-red-400">
              {errorMessage}
            </div>
          )}
          <div className="flex gap-2">
            <Button
              type="submit"
              data-testid="form-submit"
              disabled={isPending}
            >
              {isPending ? "Saving…" : mode === "create" ? "Create" : "Save"}
            </Button>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PluginRegistryPlaceholder(): React.JSX.Element {
  return (
    <Card data-testid="plugin-registry-placeholder">
      <CardHeader>
        <CardTitle className="text-sm">Plugin registry</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-sm text-muted-foreground">
          Coming F4+ — no <code className="font-mono">/api/plugins/</code> endpoint yet.
          Plugin registry exists in feature-engine config (per BRIEF §9.3) but isn&apos;t
          exposed via REST.
        </div>
      </CardContent>
    </Card>
  );
}

// H-022: env-only — no fetch from this section ever.
function ApiKeyStatusPlaceholder(): React.JSX.Element {
  return (
    <Card data-testid="api-key-status-placeholder">
      <CardHeader>
        <CardTitle className="text-sm">API key status</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2 text-sm text-muted-foreground">
          <p>
            Per H-022 — exchange API keys are <strong>env-only</strong> (read from{" "}
            <code className="font-mono">BOT_&lt;ID&gt;_BYBIT_API_KEY</code> env vars at
            service start).
          </p>
          <p>
            Key VALUES are NEVER exposed via API. Status check (env-var presence)
            deferred to F5+ task — backend would expose per-bot key-presence boolean
            only.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
