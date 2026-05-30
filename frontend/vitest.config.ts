import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Vitest baseline (H10). Unit tests for the citation engine + a couple of
// component smoke tests. No Playwright here — an end-to-end harness is a
// separate, follow-up PR.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // Only our own tests under src/ — never node_modules or .next.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
  resolve: {
    alias: {
      // Mirror the tsconfig "@/*" -> "./src/*" path mapping.
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
