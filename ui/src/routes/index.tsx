// T-411 — component showcase replacing T-410 "UI scaffold ready" placeholder.
// Smoke renders all 6 components from ui/src/components/ to verify the
// scaffold + import chain. T-412 Overview replaces this with cross-bot
// dashboard tiles.

import { type ColumnDef } from "@tanstack/react-table";
import { createFileRoute } from "@tanstack/react-router";
import * as React from "react";

import { BotSelector } from "@/components/BotSelector";
import { CorrelationIdChip } from "@/components/CorrelationIdChip";
import { DataTable } from "@/components/DataTable";
import { PriceDelta } from "@/components/PriceDelta";
import { StatusBadge } from "@/components/StatusBadge";
import { type TimeRange, TimeRangePicker } from "@/components/TimeRangePicker";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const Route = createFileRoute("/")({
  component: ComponentShowcase,
});

interface SampleRow {
  id: number;
  bot: string;
  symbol: string;
  pnl: string;
}

const SAMPLE_DATA: SampleRow[] = [
  { id: 1, bot: "alpha", symbol: "BTCUSDT", pnl: "12.34" },
  { id: 2, bot: "beta", symbol: "ETHUSDT", pnl: "-5.00" },
  { id: 3, bot: "alpha", symbol: "SOLUSDT", pnl: "0" },
];

const SAMPLE_COLUMNS: ColumnDef<SampleRow, unknown>[] = [
  { accessorKey: "id", header: "ID" },
  { accessorKey: "bot", header: "Bot" },
  { accessorKey: "symbol", header: "Symbol" },
  {
    accessorKey: "pnl",
    header: "P&L",
    cell: ({ row }) => <PriceDelta value={row.original.pnl} />,
  },
];

function ComponentShowcase(): React.JSX.Element {
  const [range, setRange] = React.useState<TimeRange>({
    from: new Date(Date.now() - 24 * 60 * 60 * 1000),
    to: new Date(),
    preset: "24h",
  });
  const [bot, setBot] = React.useState<string>("");

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Component showcase</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-muted-foreground">
          <p>T-411 component library smoke render. T-412 Overview replaces this with cross-bot dashboard tiles.</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Time range</CardTitle>
        </CardHeader>
        <CardContent>
          <TimeRangePicker value={range} onChange={setRange} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Bot selector</CardTitle>
        </CardHeader>
        <CardContent className="flex gap-4">
          <BotSelector value={bot} onChange={(v) => setBot(v as string)} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Status badges</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <StatusBadge kind="bot" status="active" />
          <StatusBadge kind="bot" status="paused" />
          <StatusBadge kind="bot" status="archived" />
          <StatusBadge kind="signal" status="validated" />
          <StatusBadge kind="signal" status="duplicate" />
          <StatusBadge kind="signal" status="invalid" />
          <StatusBadge kind="trade" status="open" />
          <StatusBadge kind="trade" status="closed" />
          <StatusBadge kind="trade" status="error" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Price deltas</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-4">
          <PriceDelta value="12.34" />
          <PriceDelta value="-5.00" />
          <PriceDelta value="0" />
          <PriceDelta value="0.000000123456789" currency="BTC" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Correlation ID chips</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <CorrelationIdChip
            correlationId="abcdef1234567890"
            onClick={() => undefined}
          />
          <CorrelationIdChip correlationId="" onClick={() => undefined} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Data table</CardTitle>
        </CardHeader>
        <CardContent>
          <DataTable columns={SAMPLE_COLUMNS} data={SAMPLE_DATA} />
        </CardContent>
      </Card>
    </div>
  );
}
