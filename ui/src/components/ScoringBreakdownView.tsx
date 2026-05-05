// T-418 — Section 7 Scoring inspector reuses this component; T-414
// Trade explorer drill-down also imports it. Extracted from T-414
// inline definitions (routes/trades.$tradeId.tsx) per OQ-3=A; verbatim
// preservation of contract + JSX + ResultBadge tone mapping so T-414
// TradeDrillDown.test.tsx test 6 passes without modification.

import * as React from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ScoringEvaluation, ScoringRuleResult } from "@/lib/api-types";
import { formatUtcDateTime } from "@/lib/format-time";

interface ScoringBreakdownViewProps {
  evaluations: ScoringEvaluation[];
}

export function ScoringBreakdownView({
  evaluations,
}: ScoringBreakdownViewProps): React.JSX.Element {
  if (evaluations.length === 0) {
    return <div className="text-sm text-muted-foreground">No scoring evaluations</div>;
  }
  return (
    <div className="space-y-4" data-testid="scoring-breakdown">
      {evaluations.map((ev) => (
        <Card key={ev.id} className="border-muted">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <span>Bot: {ev.bot_id}</span>
              <span className="text-muted-foreground">·</span>
              <span>Score: {ev.total_score.toFixed(3)}</span>
              <span className="text-muted-foreground">/ {ev.trigger_threshold.toFixed(3)}</span>
              <span className="ml-auto text-xs text-muted-foreground">
                {formatUtcDateTime(ev.evaluated_at)}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-xs">
            <div className="text-muted-foreground">
              decision: <span className="font-mono text-foreground">{ev.decision}</span>
            </div>
            <ul className="divide-y divide-border rounded-md border border-border">
              {ev.rule_results.map((r, idx) => (
                <li
                  key={`${String(ev.id)}-${String(idx)}-${r.name}`}
                  data-testid="scoring-rule-row"
                  className="flex items-center gap-2 px-3 py-1.5"
                >
                  <span className="font-medium">{r.name}</span>
                  <span className="text-muted-foreground">
                    weight {r.weight.toFixed(2)}
                  </span>
                  <span className="text-muted-foreground">
                    applied {r.applied_weight.toFixed(2)}
                  </span>
                  <ResultBadge rule={r} />
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// Per BLOCKER #1 (T-414) — `result` is loose string. Map known values
// to badge tones; unknown strings render muted.
function ResultBadge({ rule }: { rule: ScoringRuleResult }): React.JSX.Element {
  const tone = (() => {
    if (rule.result === "True") return "text-green-400";
    if (rule.result === "False") return "text-muted-foreground";
    if (rule.result === "n/a" || rule.result === "skipped") return "text-yellow-400";
    return "text-red-400";
  })();
  const errMsg =
    rule.error !== null && typeof rule.error["error"] === "string"
      ? rule.error["error"]
      : null;
  return (
    <span
      className={`ml-auto font-mono text-xs ${tone}`}
      data-result={rule.result}
      title={errMsg ?? undefined}
    >
      {rule.result}
    </span>
  );
}
