// T-411 — preset time range picker (1h / 24h / 7d / 30d / custom).
// Per BRIEF §14.4:2075 + OQ-2=A (custom presets + native HTML5
// datetime-local for custom; no calendar widget dep).
//
// Per WG#5 — UTC contract: emits `Date` instances in browser local-tz
// (from `new Date(input.value)` of `<input type="datetime-local">` which
// is naive). Consumer route MUST `.toISOString()` (returns UTC `Z`-suffix)
// BEFORE sending to backend — else FastAPI interprets the string as
// naive datetime and the backend filter window slips by the host's
// timezone offset.

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type TimeRangePreset = "1h" | "24h" | "7d" | "30d" | "custom";

export interface TimeRange {
  from: Date;
  to: Date;
  preset: TimeRangePreset;
}

interface TimeRangePickerProps {
  value: TimeRange;
  onChange: (range: TimeRange) => void;
}

const PRESETS: ReadonlyArray<{ key: Exclude<TimeRangePreset, "custom">; label: string; ms: number }> =
  [
    { key: "1h", label: "1h", ms: 60 * 60 * 1000 },
    { key: "24h", label: "24h", ms: 24 * 60 * 60 * 1000 },
    { key: "7d", label: "7d", ms: 7 * 24 * 60 * 60 * 1000 },
    { key: "30d", label: "30d", ms: 30 * 24 * 60 * 60 * 1000 },
  ];

function rangeFromPreset(preset: Exclude<TimeRangePreset, "custom">): TimeRange {
  const now = new Date();
  const ms = PRESETS.find((p) => p.key === preset)?.ms ?? 0;
  return { from: new Date(now.getTime() - ms), to: now, preset };
}

// Format Date → "yyyy-MM-ddTHH:mm" required by datetime-local input.
function toDateTimeLocal(d: Date): string {
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function TimeRangePicker({ value, onChange }: TimeRangePickerProps): React.JSX.Element {
  const handlePresetClick = (preset: Exclude<TimeRangePreset, "custom">): void => {
    onChange(rangeFromPreset(preset));
  };

  const handleCustomClick = (): void => {
    // Initialize custom range from current value or last 24h fallback.
    onChange({ from: value.from, to: value.to, preset: "custom" });
  };

  const handleFromBlur = (e: React.FocusEvent<HTMLInputElement>): void => {
    onChange({ ...value, from: new Date(e.target.value), preset: "custom" });
  };

  const handleToBlur = (e: React.FocusEvent<HTMLInputElement>): void => {
    onChange({ ...value, to: new Date(e.target.value), preset: "custom" });
  };

  return (
    <div className="flex items-center gap-2">
      {PRESETS.map((p) => (
        <Button
          key={p.key}
          variant={value.preset === p.key ? "default" : "outline"}
          size="sm"
          onClick={() => handlePresetClick(p.key)}
        >
          {p.label}
        </Button>
      ))}
      <Button
        variant={value.preset === "custom" ? "default" : "outline"}
        size="sm"
        onClick={handleCustomClick}
      >
        Custom
      </Button>
      {value.preset === "custom" && (
        <div className={cn("flex items-center gap-2")}>
          <Input
            type="datetime-local"
            defaultValue={toDateTimeLocal(value.from)}
            onBlur={handleFromBlur}
            className="h-9 w-auto"
            aria-label="from"
          />
          <span className="text-muted-foreground">→</span>
          <Input
            type="datetime-local"
            defaultValue={toDateTimeLocal(value.to)}
            onBlur={handleToBlur}
            className="h-9 w-auto"
            aria-label="to"
          />
        </div>
      )}
    </div>
  );
}
