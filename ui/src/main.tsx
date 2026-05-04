import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { routeTree } from "./routeTree.gen";
import "./styles/globals.css";

const router = createRouter({ routeTree });
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 30s stale window — UI cache TTL, NOT business-logic timing per L-001.
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

// Per WG#7 — load-bearing for TS strict + verbatimModuleSyntax. Without
// this declaration TanStack Router's typed-routes machinery breaks in
// downstream tasks (T-411+ route-parameter typing).
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("missing #root mount point");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
