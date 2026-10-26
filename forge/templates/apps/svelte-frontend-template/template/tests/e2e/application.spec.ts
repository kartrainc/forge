import { mockHealth, expectHealthyDashboard } from './health';
import { mockSession } from './session';
import { expect, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

test('application boots in a real browser', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await mockHealth(page);
    await mockSession(page);
    await page.goto('/');
    await expectHealthyDashboard(page);
    // SvelteKit can deliver server-rendered HTML before client hydration.
    await expect.poll(() => page.evaluate(() => Boolean((window as Window & { __coverage__?: Record<string, unknown> }).__coverage__))).toBe(true);
    await expect(page.locator('body')).not.toBeEmpty();
    await expect(page.locator('body')).not.toHaveText(/Internal Error|500 Internal/);
    await page.locator('a[href="/settings"]').first().click();
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Dark', exact: true }).click();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await page.getByRole('button', { name: 'Large', exact: true }).click();
    await page.getByRole('button', { name: 'Light', exact: true }).click();
    await expect(page.locator('html')).not.toHaveClass(/dark/);
    await page.locator('a[href="/"]').first().click();
    await page.getByRole('button', { name: 'Toggle theme', exact: true }).click();
    await page.getByRole('button', { name: 'Dark', exact: true }).click();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await page.getByRole('button', { name: 'Toggle theme', exact: true }).click();
    await page.getByRole('button', { name: 'System', exact: true }).click();
    await page.setViewportSize({ width: 700, height: 800 });
    await expect(page.locator('aside')).toBeVisible();
    await page.setViewportSize({ width: 390, height: 800 });
    await expect(page.locator('aside')).toHaveCount(0);
    await page.locator('a[href="/settings"]').last().click();
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    const coverage = await page.evaluate(() => (window as Window & { __coverage__?: Record<string, unknown> }).__coverage__);
    expect(coverage, 'Browser instrumentation must produce real coverage').toBeTruthy();
    const directory = process.env.FORGE_BROWSER_COVERAGE!;
    await mkdir(directory, { recursive: true });
    await writeFile(path.join(directory, String(testInfo.parallelIndex) + '-' + Date.now() + '.json'), JSON.stringify(coverage));
  }
});
