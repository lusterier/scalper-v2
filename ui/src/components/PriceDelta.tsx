// T-411 — sign-colored price delta. Per BRIEF §14.4:2078 + OQ-5=A.
//
// Per WG#6: pass-through string verbatim (NO Number()/parseFloat for
// rendering — preserves §5.3 Decimal precision from backend Decimal-as-
// string serialization). parseFloat allowed ONLY for sign detection
// (color decision); the displayed value MUST be the original `value`
// prop verbatim.

import * as React from "react";

import { cn } from "@/lib/utils";

interface PriceDeltaProps extends React.HTMLAttributes<HTMLSpanElement> {
  value: string | number;
  currency?: string;
  showSign?: boolean;
}

export function PriceDelta({
  value,
  currency = "USD",
  showSign = true,
  className,
  ...rest
}: PriceDeltaProps): React.JSX.Element {
  // Sign detection only (color); rendering uses verbatim string per WG#6.
  // parseFloat tolerates "12.34", "+12.34", "-12.34", "12.34abc" — NaN on
  // pure non-numeric falls into muted branch (Edge case #11).
  const sign = parseFloat(String(value));
  const tone =
    Number.isNaN(sign) || sign === 0
      ? "text-muted-foreground"
      : sign > 0
        ? "text-green-400"
        : "text-red-400";

  // Display as-is. Prepend "+" only when sign is positive AND showSign
  // AND the value string doesn't already lead with "+" or "-".
  const valueStr = String(value);
  const needsPlusPrefix =
    showSign &&
    sign > 0 &&
    !valueStr.startsWith("+") &&
    !valueStr.startsWith("-");
  const displayValue = needsPlusPrefix ? `+${valueStr}` : valueStr;

  return (
    <span
      className={cn("font-mono text-sm", tone, className)}
      data-value={valueStr}
      data-currency={currency}
      {...rest}
    >
      {displayValue} {currency}
    </span>
  );
}
