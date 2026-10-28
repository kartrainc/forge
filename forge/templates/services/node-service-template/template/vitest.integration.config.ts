import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/integration/**/*.test.ts"],
    exclude: ["**/node_modules/**"],
    environment: "node",
    env: { NODE_ENV: "testing" },
    setupFiles: ["tests/integration/setup-env.ts"],
    passWithNoTests: false,
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
