// Vitest setup file — runs before each test file.
// Per WG#8: import globals.css here so vite resolves the module without
// raising; jsdom doesn't execute Tailwind / process @import / @tailwind
// directives, but the import path is sanity-checked.

import "@testing-library/jest-dom";
import { vi } from "vitest";

import "../styles/globals.css";

// jsdom doesn't implement window.scrollTo; TanStack Router scroll-
// restoration calls it on every navigation. Stub at setup time so all
// route renders succeed in tests.
Object.defineProperty(window, "scrollTo", {
  value: vi.fn(),
  writable: true,
});
