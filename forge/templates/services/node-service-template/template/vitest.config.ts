import { defineConfig } from "vitest/config";

export default defineConfig({
	test: {
		globals: false,
		environment: "node",
		include: ["tests/**/*.test.ts"],
		// CI prints the console of failing tests only: a runner's log slows down on large output
		silent: /^(1|true)$/i.test(process.env.CI ?? "") ? "passed-only" : false,
		// Vitest defaults ``NODE_ENV=test`` which the AppConfigSchema's
		// strict enum (``development|testing|staging|production``) rejects
		// at config load. Map it to ``testing`` once before any test
		// imports the config module, so the loader's ``z.parse`` succeeds.
		env: { NODE_ENV: "testing" },
	},
});
