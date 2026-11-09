import { test, expect } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

test.describe('providers', () => {
  test('creates a provider', async ({ page }) => {
    await test.step('open the form', async () => {});
  });
  test('syncs the catalog', async ({ page }) => {});
});
