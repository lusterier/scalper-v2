// T-516a2 — internal helper. Generic dl pair (label + value).
// Internal to trade-drill module; NOT exported via barrel index per
// plan-reviewer Gate 1 WG#6 (keeps Row internal; future re-use must go
// through public API explicitly).

import * as React from "react";

export function Row({
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
