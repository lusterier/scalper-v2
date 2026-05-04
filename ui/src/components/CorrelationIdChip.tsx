// T-411 — clickable chip filtering audit log to events with same correlation_id.
// Per BRIEF §14.4:2079 + OQ-6=A.
//
// Per WG#3 — lazy useNavigate resolution: when `onClick` prop is provided,
// useNavigate hook is NOT called at all (preserves chip's reusability
// outside RouterProvider context — e.g., standalone tests, future static
// render contexts). Only the default (no override) branch invokes the
// hook + navigates to /audit?correlation_id=<encoded>.

import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { cn } from "@/lib/utils";

interface CorrelationIdChipProps extends React.HTMLAttributes<HTMLButtonElement> {
  correlationId: string;
  onClick?: () => void;
}

const TRUNCATE_LENGTH = 8;

export function CorrelationIdChip({
  correlationId,
  onClick,
  className,
  ...rest
}: CorrelationIdChipProps): React.JSX.Element {
  const truncated =
    correlationId.length > TRUNCATE_LENGTH
      ? `${correlationId.slice(0, TRUNCATE_LENGTH)}…`
      : correlationId;

  // Lazy useNavigate per WG#3 — only invoke hook when no onClick override.
  // useNavigate is a hook so call is unconditional at top-level; we WORK
  // AROUND React's rules-of-hooks by using a separate component for the
  // default branch. Chip with onClick override never mounts the navigator.
  if (onClick !== undefined) {
    return (
      <ChipButton truncated={truncated} correlationId={correlationId} onClick={onClick} className={className} {...rest} />
    );
  }
  return <NavigatingChip truncated={truncated} correlationId={correlationId} className={className} {...rest} />;
}

interface InternalChipProps extends React.HTMLAttributes<HTMLButtonElement> {
  truncated: string;
  correlationId: string;
}

function ChipButton({
  truncated,
  correlationId,
  onClick,
  className,
  ...rest
}: InternalChipProps & { onClick: () => void }): React.JSX.Element {
  const isEmpty = correlationId.length === 0;
  return (
    <button
      type="button"
      title={correlationId || "no-corr-id"}
      onClick={isEmpty ? undefined : onClick}
      disabled={isEmpty}
      className={cn(
        "inline-flex items-center rounded-md bg-muted px-2 py-0.5 font-mono text-xs ring-1 ring-border hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      data-correlation-id={correlationId}
      {...rest}
    >
      {isEmpty ? "no-corr-id" : truncated}
    </button>
  );
}

function NavigatingChip({
  truncated,
  correlationId,
  className,
  ...rest
}: InternalChipProps): React.JSX.Element {
  // useNavigate ONLY in default branch per WG#3; chip with onClick override
  // never mounts this component, so chip stays usable outside RouterProvider.
  const navigate = useNavigate();
  const isEmpty = correlationId.length === 0;
  const handleClick = (): void => {
    if (isEmpty) return;
    // T-419 audit log viewer route does not yet exist in routeTree.gen.
    // Cast `to` to bypass TanStack Router's typed-route check until T-419
    // adds /audit. T-419 will retire this `as never` cast.
    void navigate({
      to: "/audit" as never,
      search: { correlation_id: correlationId } as never,
    });
  };
  return (
    <button
      type="button"
      title={correlationId || "no-corr-id"}
      onClick={handleClick}
      disabled={isEmpty}
      className={cn(
        "inline-flex items-center rounded-md bg-muted px-2 py-0.5 font-mono text-xs ring-1 ring-border hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      data-correlation-id={correlationId}
      {...rest}
    >
      {isEmpty ? "no-corr-id" : truncated}
    </button>
  );
}
