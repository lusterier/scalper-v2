import { describe, expect, it } from "vitest";

import { formatUtcDateTime, formatUtcTimeOnly } from "../lib/format-time";

describe("format-time", () => {
  it("formatUtcDateTime returns 'yyyy-MM-dd HH:MM:SS UTC' for valid ISO", () => {
    expect(formatUtcDateTime("2026-05-05T14:30:45Z")).toBe("2026-05-05 14:30:45 UTC");
    expect(formatUtcDateTime("2026-05-05T14:30:45.123Z")).toBe("2026-05-05 14:30:45 UTC");
    expect(formatUtcDateTime("2026-05-05T14:30:45+00:00")).toBe("2026-05-05 14:30:45 UTC");
  });

  it("formatUtcTimeOnly returns 'HH:MM:SS UTC' (SignalFeed compatibility lock per BLOCKER #1 path A)", () => {
    // No date components — locks identity with T-413 SignalFeed inline
    // formatReceivedAt that this helper replaced.
    expect(formatUtcTimeOnly("2026-05-05T14:30:45Z")).toBe("14:30:45 UTC");
    expect(formatUtcTimeOnly("2026-05-05T00:00:00Z")).toBe("00:00:00 UTC");
    expect(formatUtcTimeOnly("2026-05-05T23:59:59Z")).toBe("23:59:59 UTC");
    // Sanity — output never includes a year/month/day component.
    expect(formatUtcTimeOnly("2026-05-05T14:30:45Z")).not.toContain("2026");
  });

  it("formatUtcDateTime + formatUtcTimeOnly handle epoch zero (1970-01-01) edge case", () => {
    expect(formatUtcDateTime("1970-01-01T00:00:00Z")).toBe("1970-01-01 00:00:00 UTC");
    expect(formatUtcTimeOnly("1970-01-01T00:00:00Z")).toBe("00:00:00 UTC");
  });
});
