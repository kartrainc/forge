import { defineConfig } from "vitest/config";

export default defineConfig({
	test: {
		globals: false,
		environment: "node",
		include: ["tests/**/*.test.ts"],
		// Vitest defaults ``NODE_ENV=test`` which the AppConfigSchema's
		// strict enum (``development|testing|staging|production``) rejects
		// at config load. Map it to ``testing`` once before any test
		// imports the config module, so the loader's ``z.parse`` succeeds.
		env: { NODE_ENV: "testing" },
	},
});
