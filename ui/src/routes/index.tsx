import { createFileRoute } from "@tanstack/react-router";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// `/` placeholder — "UI scaffold ready" per T-410 acceptance criterion #8.
// T-412 Overview replaces this with the cross-bot dashboard tiles.

export const Route = createFileRoute("/")({
  component: ScaffoldReady,
});

function ScaffoldReady() {
  return (
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle>UI scaffold ready</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm text-muted-foreground">
        <p>
          React 18 + Vite 5.4 + TypeScript strict + Tailwind + shadcn/ui +
          TanStack Router/Query + Zustand + Recharts.
        </p>
        <p>
          Subsequent UI tasks (T-411 component library, T-412..T-420 nine
          dashboard sections) land on this scaffold.
        </p>
      </CardContent>
    </Card>
  );
}
