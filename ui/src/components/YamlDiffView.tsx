// T-515 — Strategy editor diff-against-live UI per BRIEF §19:2575.
// Side-by-side line-level diff component with Tailwind utility-class
// highlighting (no new dependency; inline LCS DP).
//
// Algorithm: two-pass
//   Pass 1 — O(a×b) LCS DP over `current.split("\n")` + `draft.split("\n")`
//   produces ordered raw `{equal, added, removed}` ops.
//   Pass 2 — modify-collapse: isolated `removed[k]` + `added[k+1]` pair
//   (or vice versa) where NEITHER is part of a contiguous remove/add
//   block collapses into a single `modified` op carrying both aLine +
//   bLine. Cleaner UX: single-line edit shows as one yellow row instead
//   of separate red+green rows. Multi-line replacements (3 removed + 3
//   added contiguous) stay as separate ops.
//
// Fresh-bot edge case: `current.trim() === ""` short-circuits the LCS
// path — left column renders `freshBotPlaceholder` text (default
// "(no active config — first version)") inside `data-testid="diff-current"`
// wrapper to preserve existing Strategy.test.tsx:103 assertion. Right
// column renders entire draft as added (green-bg).

import * as React from "react";

import { cn } from "@/lib/utils";

export interface YamlDiffViewProps {
  current: string;
  draft: string;
  freshBotPlaceholder?: string;
  className?: string;
}

type DiffOp =
  | { kind: "equal"; aLine: string; bLine: string }
  | { kind: "added"; bLine: string }
  | { kind: "removed"; aLine: string }
  | { kind: "modified"; aLine: string; bLine: string };

function diffLines(a: string[], b: string[]): DiffOp[] {
  // Pass 1 — LCS DP table
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }
  // Backtrack to ordered raw ops
  const raw: DiffOp[] = [];
  let i = m;
  let j = n;
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      raw.unshift({ kind: "equal", aLine: a[i - 1]!, bLine: b[j - 1]! });
      i--;
      j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      raw.unshift({ kind: "removed", aLine: a[i - 1]! });
      i--;
    } else {
      raw.unshift({ kind: "added", bLine: b[j - 1]! });
      j--;
    }
  }
  while (i > 0) {
    raw.unshift({ kind: "removed", aLine: a[i - 1]! });
    i--;
  }
  while (j > 0) {
    raw.unshift({ kind: "added", bLine: b[j - 1]! });
    j--;
  }
  // Pass 2 — modify-collapse: isolated removed+added pair (or added+removed)
  // where neither is part of contiguous block.
  const collapsed: DiffOp[] = [];
  let k = 0;
  while (k < raw.length) {
    const cur = raw[k]!;
    const next = raw[k + 1];
    const prev = collapsed[collapsed.length - 1];
    const after = raw[k + 2];
    const isIsolatedPair =
      next !== undefined &&
      ((cur.kind === "removed" && next.kind === "added") ||
        (cur.kind === "added" && next.kind === "removed")) &&
      (prev === undefined || prev.kind === "equal" || prev.kind === "modified") &&
      (after === undefined || after.kind === "equal");
    if (isIsolatedPair) {
      const removedLine = cur.kind === "removed" ? cur.aLine : (next as { kind: "removed"; aLine: string }).aLine;
      const addedLine = cur.kind === "added" ? cur.bLine : (next as { kind: "added"; bLine: string }).bLine;
      collapsed.push({ kind: "modified", aLine: removedLine, bLine: addedLine });
      k += 2;
    } else {
      collapsed.push(cur);
      k++;
    }
  }
  return collapsed;
}

const _LINE_COMMON = "px-2 py-0.5 font-mono whitespace-pre";

function _leftClass(op: DiffOp): string {
  if (op.kind === "removed") return cn(_LINE_COMMON, "bg-red-50");
  if (op.kind === "modified") return cn(_LINE_COMMON, "bg-yellow-50");
  return _LINE_COMMON;
}

function _rightClass(op: DiffOp): string {
  if (op.kind === "added") return cn(_LINE_COMMON, "bg-green-50");
  if (op.kind === "modified") return cn(_LINE_COMMON, "bg-yellow-50");
  return _LINE_COMMON;
}

function _leftText(op: DiffOp): string {
  if (op.kind === "added") return "";
  return "aLine" in op ? op.aLine : "";
}

function _rightText(op: DiffOp): string {
  if (op.kind === "removed") return "";
  return "bLine" in op ? op.bLine : "";
}

export function YamlDiffView({
  current,
  draft,
  freshBotPlaceholder = "(no active config — first version)",
  className,
}: YamlDiffViewProps): React.JSX.Element {
  const isFreshBot = current.trim() === "";
  const ops = React.useMemo<DiffOp[]>(() => {
    if (isFreshBot) {
      return draft.split("\n").map((line) => ({ kind: "added", bLine: line }) as DiffOp);
    }
    return diffLines(current.split("\n"), draft.split("\n"));
  }, [current, draft, isFreshBot]);

  return (
    <div
      className={cn("grid grid-cols-2 gap-2 text-xs", className)}
      data-testid="yaml-diff-view"
    >
      <div>
        <div className="mb-1 text-muted-foreground">Current</div>
        <div
          data-testid="diff-current"
          className="max-h-[400px] overflow-auto rounded-md border border-border bg-muted/20"
        >
          {isFreshBot ? (
            <div className={_LINE_COMMON}>{freshBotPlaceholder}</div>
          ) : (
            ops.map((op, idx) => (
              <div key={`l-${idx}`} className={_leftClass(op)}>
                {_leftText(op) || " "}
              </div>
            ))
          )}
        </div>
      </div>
      <div>
        <div className="mb-1 text-muted-foreground">New (editing)</div>
        <div
          data-testid="diff-new"
          className="max-h-[400px] overflow-auto rounded-md border border-border bg-muted/20"
        >
          {ops.map((op, idx) => (
            <div key={`r-${idx}`} className={_rightClass(op)}>
              {_rightText(op) || " "}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
