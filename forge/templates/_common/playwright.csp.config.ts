import { defineConfig } from '@playwright/test';
// The browser suites against the production build under the security.csp
// policy (scripts/serve-csp.mjs); run `npm run build` first. The build keeps
// the project's authentication setting and nothing serves /auth/* here, so the
// suites answer the session in the page (tests/e2e/session.ts). Adapt this
// file to the specs, projects and builds a production run can serve; an
// update replaces it only while it is untouched. A policy kept outside
// nginx.conf is named in csp-check.json (scripts/serve-csp.mjs).
export default defineConfig({
  testDir: './tests/e2e', workers: 1, retries: 0, reporter: 'json',
  use: { baseURL: 'http://127.0.0.1:4174', browserName: 'chromium' },
  webServer: { command: 'node scripts/serve-csp.mjs 4174', url: 'http://127.0.0.1:4174', reuseExistingServer: false },
});
