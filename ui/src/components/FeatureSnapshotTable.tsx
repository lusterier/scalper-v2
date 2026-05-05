// T-418 — Per-evaluation feature snapshot key-value table per OQ-4=B.
// Backend `scoring_evaluations.feature_snapshot` is JSONB
// `dict[str, Any]` (feature_name → value at scoring time). Render as
// sorted key-value table; empty snapshot → placeholder (per WG#9);
// nested object/array → JSON.stringify truncated to 80 chars + tooltip
// (per WG#9 — browser-native title attribute, no Radix Tooltip
// overhead).

import * as React from "react";

interface FeatureSnapshotTableProps {
  snapshot: Record<string, unknown>;
}

interface FormattedValue {
  display: string;
  full: string;
  tone?: string;
}

function formatValue(v: unknown): FormattedValue {
  // Per WG#10 — null renders literal "null" with muted tone.
  if (v === null) return { display: "null", full: "null", tone: "text-muted-foreground" };
  if (typeof v === "number") {
    const display = Number.isFinite(v) ? v.toFixed(6) : String(v);
    return { display, full: display };
  }
  if (typeof v === "string") return { display: v, full: v };
  if (typeof v === "boolean") {
    const s = String(v);
    return { display: s, full: s };
  }
  // Object / array → JSON.stringify; truncate to 80 chars.
  const str = JSON.stringify(v);
  if (str.length > 80) {
    return { display: `${str.slice(0, 80)}…`, full: str };
  }
  return { display: str, full: str };
}

export function FeatureSnapshotTable({
  snapshot,
}: FeatureSnapshotTableProps): React.JSX.Element {
  const sortedKeys = Object.keys(snapshot).sort();

  if (sortedKeys.length === 0) {
    return (
      <div data-testid="feature-snapshot-empty" className="text-sm text-muted-foreground">
        (empty feature_snapshot)
      </div>
    );
  }

  return (
    <table data-testid="feature-snapshot-table" className="w-full text-xs">
      <thead className="text-muted-foreground">
        <tr>
          <th className="px-3 py-1.5 text-left font-medium">Feature</th>
          <th className="px-3 py-1.5 text-left font-medium">Value</th>
        </tr>
      </thead>
      <tbody>
        {sortedKeys.map((key) => {
          const formatted = formatValue(snapshot[key]);
          return (
            <tr
              key={key}
              data-testid="feature-snapshot-row"
              className="border-t border-border"
            >
              <td className="px-3 py-1 font-mono">{key}</td>
              <td
                className={`px-3 py-1 font-mono ${formatted.tone ?? ""}`}
                title={formatted.full !== formatted.display ? formatted.full : undefined}
              >
                {formatted.display}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
