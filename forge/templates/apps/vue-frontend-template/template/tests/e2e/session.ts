import type { Page } from '@playwright/test';

/**
 * The signed-in user as the gateway's `/auth/userinfo` answers it (the
 * Gatekeeper BFF's fields), the same person as the app's development user.
 */
export const SESSION_USER = {
  userId: '00000000-0000-0000-0000-000000000001',
  email: 'dev@localhost',
  preferredUsername: 'dev-user',
  givenName: 'Dev',
  familyName: 'User',
  roles: ['admin', 'user'],
  customerId: '00000000-0000-0000-0000-000000000001',
  orgId: null,
};

/**
 * Answer the gateway's session in the page. The browser suites run the app with
 * the project's authentication setting and no gateway serves /auth/* here: with
 * authentication on, the app asks `/auth/userinfo` who is signed in; with it
 * off, it never asks. `/auth/session` answers 404, a gateway without session
 * timeouts, so the session-timeout dialog, where a project mounts it, stays
 * inactive. A spec that routes `/auth/*` itself after this call wins, since
 * Playwright tries the routes registered last first.
 */
export async function mockSession(page: Page, user: Record<string, unknown> = SESSION_USER) {
  await page.route(url => url.pathname === '/auth/userinfo', route => route.fulfill({ json: user }));
  await page.route(url => url.pathname === '/auth/session', route => route.fulfill({ status: 404, json: { message: 'Not found' } }));
}
