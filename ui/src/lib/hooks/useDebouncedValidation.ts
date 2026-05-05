// T-416 — Debounced live YAML validation hook for Strategy editor.
// Per OQ-3=A: 500ms debounce → POST /api/configs/validate. Per WG#3:
// AbortController threading + per WG#4 empty-text synchronous skip.

import * as React from "react";

import { apiFetch } from "@/lib/api-client";
import type { ConfigValidateResponse } from "@/lib/api-types";

interface UseDebouncedValidationResult {
  valid: boolean;
  errors: string[];
  parsedVersion: number | null;
  isPending: boolean;
}

const EMPTY_RESULT: UseDebouncedValidationResult = {
  valid: false,
  errors: ["yaml_text empty"],
  parsedVersion: null,
  isPending: false,
};

// Per L-001 + §N9 — named constant; UI cache TTL aligned with BRIEF
// §14.3:2064 "live" responsiveness budget. NOT a business-logic timing
// knob (§N3/§N9 active control covers signal/order/scoring timing).
export const VALIDATION_DEBOUNCE_MS = 500;

export function useDebouncedValidation(
  yamlText: string,
  botId: string,
  debounceMs = VALIDATION_DEBOUNCE_MS,
): UseDebouncedValidationResult {
  const [result, setResult] = React.useState<UseDebouncedValidationResult>(EMPTY_RESULT);

  React.useEffect(() => {
    // Per WG#4 — synchronous short-circuit BEFORE 500ms debounce.
    // Saves backend cycles + avoids spurious red error panel during
    // typing-then-erase rapid-fire. Backend Pydantic min_length=1
    // would reject anyway with 422.
    if (yamlText.trim() === "") {
      setResult(EMPTY_RESULT);
      return;
    }

    setResult((prev) => ({ ...prev, isPending: true }));

    // Per WG#3 — fresh AbortController each effect iteration.
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const response = await apiFetch<ConfigValidateResponse>(
            "/api/configs/validate",
            {
              method: "POST",
              body: { bot_id: botId, yaml_text: yamlText },
              signal: controller.signal,
            },
          );
          setResult({
            valid: response.valid,
            errors: response.errors,
            parsedVersion: response.parsed_version,
            isPending: false,
          });
        } catch (err) {
          // Per WG#3 — AbortError early-return without state update;
          // preserves prior result, prevents flicker.
          if (err instanceof Error && err.name === "AbortError") {
            return;
          }
          setResult({
            valid: false,
            errors: [err instanceof Error ? err.message : String(err)],
            parsedVersion: null,
            isPending: false,
          });
        }
      })();
    }, debounceMs);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [yamlText, botId, debounceMs]);

  return result;
}
