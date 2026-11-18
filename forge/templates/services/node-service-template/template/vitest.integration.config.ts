import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/integration/**/*.test.ts"],
    exclude: ["**/node_modules/**"],
    environment: "node",
    env: { NODE_ENV: "testing" },
    setupFiles: ["tests/integration/setup-env.ts"],
    passWithNoTests: false,
    // CI prints the console of failing tests only: a runner's log slows down on large output
    silent: /^(1|true)$/i.test(process.env.CI ?? "") ? "passed-only" : false,
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts", "../../packages/*/src/**/*.ts", "packages/*/src/**/*.ts"],
      allowExternal: true,
      exclude: ["**/*.test.ts", "**/*.d.ts"],
      reportsDirectory: "coverage/integration",
      reporter: ["json", "lcov", "text"],
    },
  },
});
