// T-517a2 — symbol picker landing for per-symbol best-variant aggregate
// (BRIEF §13.6 second bullet "which variant would have been best over last
// N trades?"). Free-text input + Go button → navigates to
// `/shadow/aggregate/$symbol`. Mirror paper-trades.index minimal-card
// pattern modulo no API call (purely client-side until navigate).

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export const Route = createFileRoute("/shadow/aggregate/")({
  component: ShadowAggregateIndexPage,
});

function ShadowAggregateIndexPage(): React.JSX.Element {
  const navigate = useNavigate();
  const [symbol, setSymbol] = React.useState("");
  const trimmed = symbol.trim();

  const handleGo = (): void => {
    if (trimmed === "") return;
    // WG#1 — uppercase normalization. Backend predicate
    // `COALESCE(t.symbol, pt.symbol) = $1::text` is case-sensitive
    // (services/analytics_api/app/routers/shadow_aggregate.py + sibling
    // packages/db/queries/shadow.py:625-640 builder). Lowercase input
    // would yield a silent empty result with no UI feedback. Bybit /
    // ZenAlgo canonical symbol form is uppercase; the placeholder
    // `BTCUSDT` already signals this — enforcement matches the implicit
    // contract.
    void navigate({
      to: "/shadow/aggregate/$symbol",
      params: { symbol: trimmed.toUpperCase() },
    });
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Per-symbol best-variant aggregate</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label
              htmlFor="aggregate-symbol-input"
              className="text-xs text-muted-foreground"
            >
              Symbol (e.g. BTCUSDT)
            </label>
            <Input
              id="aggregate-symbol-input"
              data-testid="aggregate-symbol-input"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleGo();
              }}
              placeholder="BTCUSDT"
              className="h-10 w-[200px]"
            />
          </div>
          <Button
            data-testid="aggregate-symbol-go"
            onClick={handleGo}
            disabled={trimmed === ""}
          >
            View aggregate
          </Button>
        </CardContent>
      </Card>
      <p className="text-xs text-muted-foreground">
        Pick a symbol to view per-variant aggregate metrics for that instrument.
      </p>
    </div>
  );
}
