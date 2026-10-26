import { mockHealth, expectHealthyDashboard } from './health';
import { mockSession } from './session';
import { expect, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

/** Browser contract for each generated CRUD feature, with a stateful HTTP fixture. */
export function testCrud(plural: string, singular: string, backend: string) {
  test(`${plural}: create, search, edit and delete through the UI`, async ({ page }, testInfo) => {
    const id = '550e8400-e29b-41d4-a716-446655440099';
    let item: Record<string, unknown> | null = null;
    const writes: { method: string; body: Record<string, unknown> | null }[] = [];
    const endpoint = `/api/${backend}/v1/${plural}`;
    await page.route(url => url.pathname === endpoint || url.pathname.startsWith(endpoint + '/'), async route => {
      const method = route.request().method();
      if (method === 'POST' || method === 'PATCH') {
        const body = route.request().postDataJSON();
        writes.push({ method, body });
        item = { id, status: 'DRAFT', tags: [], customer_id: id, user_id: id,
          created_at: '2026-01-01T00:00:00Z', updated_at: null, ...item, ...body };
        await route.fulfill({ status: method === 'POST' ? 201 : 200, json: item });
      } else if (method === 'DELETE') {
        writes.push({ method, body: null });
        item = null;
        await route.fulfill({ status: 204 });
      } else if (new URL(route.request().url()).pathname === endpoint) {
        const search = new URL(route.request().url()).searchParams.get('search') || '';
        const items = item && String(item.name).includes(search) ? [item] : [];
        await route.fulfill({ json: { items, total: items.length, skip: 0, limit: 50, has_more: false } });
      } else {
        await route.fulfill({ status: item ? 200 : 404, json: item || { message: 'Not found' } });
      }
    });
    const field = (name: string) => page.locator(`[data-test="${singular}-${name}"], [data-testid="${singular}-${name}"]`);
    try {
      await mockHealth(page);
      await mockSession(page);
      await page.goto('/');
      await expectHealthyDashboard(page);
      await expect.poll(() => page.evaluate(() => Boolean((window as Window & { __coverage__?: Record<string, unknown> }).__coverage__))).toBe(true);
      await page.locator(`a[href="/${plural}"]`).first().click();
      await page.getByText(`New ${singular[0].toUpperCase() + singular.slice(1)}`, { exact: true }).click();
      await field('name-input').fill('Quality example');
      await field('description-input').fill('A browser-created record');
      await field('submit-btn').click();
      await expect(page).toHaveURL(new RegExp(`/${plural}/${id}$`));
      await expect(page.getByText('Quality example', { exact: true }).first()).toBeVisible();
      expect(writes[0]).toMatchObject({ method: 'POST', body: { name: 'Quality example', description: 'A browser-created record' } });
      await field('edit-btn').click();
      const nameInput = page.locator(`[data-test="${singular}-edit-name-input"], #edit-name`);
      await nameInput.fill('Updated example');
      await field('save-btn').click();
      await expect(page.getByText('Updated example', { exact: true }).first()).toBeVisible();
      expect(writes[1]).toMatchObject({ method: 'PATCH', body: { name: 'Updated example' } });
      await page.locator(`a[href="/${plural}"]`).first().click();
      await page.getByPlaceholder(new RegExp('Search', 'i')).fill('Updated');
      await expect(page.getByText('Updated example', { exact: true }).first()).toBeVisible();
      await page.getByText('Updated example', { exact: true }).first().click();
      await field('delete-btn').click();
      await page.getByRole('button', { name: 'Delete', exact: true }).last().click();
      await expect(page).toHaveURL(new RegExp(`/${plural}$`));
      await expect(page.getByText('Updated example', { exact: true })).toHaveCount(0);
      expect(writes.map(write => write.method)).toEqual(['POST', 'PATCH', 'DELETE']);
    } finally {
      const coverage = await page.evaluate(() => (window as Window & { __coverage__?: Record<string, unknown> }).__coverage__);
      expect(coverage, 'Browser instrumentation must produce real coverage').toBeTruthy();
      const directory = process.env.FORGE_BROWSER_COVERAGE!;
      await mkdir(directory, { recursive: true });
      await writeFile(path.join(directory, `${testInfo.parallelIndex}-${Date.now()}.json`), JSON.stringify(coverage));
    }
  });
}
