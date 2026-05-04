// T-411 — single or multi-select bot picker. Per BRIEF §14.4:2076 +
// OQ-3=A (fetches /api/bots/ via TanStack Query; 30s staleTime inherits
// global QueryClient default from main.tsx).
//
// Multi-select uses shadcn Dialog + checkbox list (per WG#2 — no new
// shadcn primitive; Dialog is in T-410 baseline).

import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiFetch } from "@/lib/api-client";
import type { BotListResponse } from "@/lib/api-types";

interface BotSelectorProps {
  value: string | string[];
  onChange: (selection: string | string[]) => void;
  multi?: boolean;
  placeholder?: string;
}

export function BotSelector({
  value,
  onChange,
  multi = false,
  placeholder = "Select bot(s)",
}: BotSelectorProps): React.JSX.Element {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["bots"],
    queryFn: () => apiFetch<BotListResponse>("/api/bots/"),
  });

  if (isLoading) {
    return (
      <Select disabled>
        <SelectTrigger className="w-[200px]">
          <SelectValue placeholder="Loading..." />
        </SelectTrigger>
      </Select>
    );
  }

  if (isError) {
    return (
      <Select disabled>
        <SelectTrigger
          className="w-[200px] border-red-500"
          title={error instanceof Error ? error.message : "Failed to load bots"}
        >
          <SelectValue placeholder="Failed to load bots" />
        </SelectTrigger>
      </Select>
    );
  }

  const bots = data?.bots ?? [];

  if (bots.length === 0) {
    return (
      <Select disabled>
        <SelectTrigger className="w-[200px]">
          <SelectValue placeholder="no bots configured" />
        </SelectTrigger>
      </Select>
    );
  }

  if (multi) {
    return <MultiSelect bots={bots} value={value as string[]} onChange={onChange} placeholder={placeholder} />;
  }

  return (
    <Select
      value={typeof value === "string" ? value : ""}
      onValueChange={(v) => onChange(v)}
    >
      <SelectTrigger className="w-[200px]">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {bots.map((bot) => (
          <SelectItem key={bot.bot_id} value={bot.bot_id}>
            {bot.display_name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

interface MultiSelectProps {
  bots: ReadonlyArray<{ bot_id: string; display_name: string }>;
  value: string[];
  onChange: (selection: string[]) => void;
  placeholder: string;
}

function MultiSelect({ bots, value, onChange, placeholder }: MultiSelectProps): React.JSX.Element {
  const [open, setOpen] = React.useState(false);
  const selected = new Set(value);

  const toggle = (botId: string): void => {
    const next = new Set(selected);
    if (next.has(botId)) next.delete(botId);
    else next.add(botId);
    onChange(Array.from(next));
  };

  const summary =
    value.length === 0
      ? placeholder
      : value.length === 1
        ? (bots.find((b) => b.bot_id === value[0])?.display_name ?? value[0])
        : `${String(value.length)} bots selected`;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          className="inline-flex h-10 w-[200px] items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm hover:bg-accent"
        >
          <span className="truncate">{summary}</span>
        </button>
      </DialogTrigger>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Select bots</DialogTitle>
        </DialogHeader>
        <div className="max-h-80 overflow-y-auto">
          {bots.map((bot) => (
            <label
              key={bot.bot_id}
              className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 hover:bg-accent"
            >
              <input
                type="checkbox"
                checked={selected.has(bot.bot_id)}
                onChange={() => toggle(bot.bot_id)}
              />
              <span className="text-sm">{bot.display_name}</span>
            </label>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
