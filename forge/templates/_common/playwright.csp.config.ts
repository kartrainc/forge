import { defineConfig } from '@playwright/test';
// The browser suites against the production build under the security.csp
// policy (scripts/serve-csp.mjs); run `VITE_AUTH_DISABLED=true npm run build`
// first: nothing serves /auth/* here.
export default defineConfig({
  testDir: './tests/e2e', workers: 1, retries: 0, reporter: 'json',
  use: { baseURL: 'http://127.0.0.1:4174', browserName: 'chromium' },
  webServer: { command: 'node scripts/serve-csp.mjs 4174', url: 'http://127.0.0.1:4174', reuseExistingServer: false },
});
