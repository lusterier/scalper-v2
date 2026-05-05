// T-414 — UTC timestamp formatting helpers per BRIEF §14.2 dashboard
// convention + §N1 audit-trail "explicit UTC suffix" rule. Two helpers
// because feed UX (SignalFeed) wants compact time-only while drill-down
// audit-grade surfaces (trade summary / signal detail / scoring eval)
// want full date+time. F5+ CEST/UTC toggle replaces both.

// Compact time-only "HH:MM:SS UTC" — for high-frequency feeds where
// date is implicit from listing scope. T-413 SignalFeed migrates to
// this helper (drop-in identical output to its inline `formatReceivedAt`
// per pass-3 plan-reviewer WG#3).
export function formatUtcTimeOnly(iso: string): string {
  const d = new Date(iso);
  const HH = String(d.getUTCHours()).padStart(2, "0");
  const MM = String(d.getUTCMinutes()).padStart(2, "0");
  const SS = String(d.getUTCSeconds()).padStart(2, "0");
  return `${HH}:${MM}:${SS} UTC`;
}

// Full "yyyy-MM-dd HH:MM:SS UTC" — for audit-trail-grade displays where
// the date matters (mixed-day events on a single screen).
export function formatUtcDateTime(iso: string): string {
  const d = new Date(iso);
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${formatUtcTimeOnly(iso)}`;
}
