import { test, expect } from '@playwright/test';

test('demo is explicit, blocks unbalanced drafts, and safely renders posted narratives', async ({ page }) => {
  await page.goto('/?demo=1');
  await expect(page.locator('#portal-mode')).toContainText('Synthetic data');
  await page.getByRole('button', { name: /02 Transactions/ }).click();
  await expect(page.locator('.row-amount')).toHaveCount(2);
  const post = page.getByRole('button', { name: 'Post Transaction', exact: true });
  await expect(post).toBeDisabled();
  await page.locator('.row-amount').nth(0).fill('25.50');
  await expect(post).toBeDisabled();
  await page.locator('.row-amount').nth(1).fill('25.50');
  await page.locator('#txn-narrative').fill('<img src=x onerror="window.injected=true">');
  await expect(post).toBeEnabled();
  await post.click();
  await expect(page.locator('#post-txn-message')).toHaveText('Posted.');
  await expect(page.locator('#txn-table-body')).toContainText('<img src=x');
  expect(await page.evaluate(() => 'injected' in window)).toBe(false);
});

test('live portal maps API envelopes and reuses a retry key after an ambiguous failure', async ({ page }) => {
  const accounts = [{ account_id: '11111111-1111-4111-8111-111111111111', account_number: 'Cash', account_type: 'ASSET' }, { account_id: '22222222-2222-4222-8222-222222222222', account_number: 'Revenue', account_type: 'REVENUE' }];
  const keys = [];
  await page.route('**/portal/session', route => route.fulfill({ json: { authenticated: true, configured: true } }));
  await page.route('**/api/v1/accounts?*', route => route.fulfill({ json: { accounts, total: 2 } }));
  await page.route('**/api/v1/reconciliation/exceptions', route => route.fulfill({ json: { exceptions: [] } }));
  await page.route('**/api/v1/transactions', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { transactions: [] } });
    keys.push(route.request().headers()['idempotency-key']);
    expect(route.request().headers()['x-api-key']).toBeUndefined();
    return route.fulfill(keys.length === 1 ? { status: 503, json: {} } : { status: 201, json: { status: 'POSTED' } });
  });
  await page.goto('/');
  await expect(page.locator('#portal-mode')).toContainText('Live workspace');
  await page.getByRole('button', { name: /02 Transactions/ }).click();
  await expect(page.locator('.row-account').first()).toContainText('Cash');
  await page.locator('.row-account').nth(1).selectOption(accounts[1].account_id);
  await page.locator('.row-amount').nth(0).fill('10');
  await page.locator('.row-amount').nth(1).fill('10');
  await page.getByRole('button', { name: 'Post Transaction', exact: true }).click();
  await expect(page.locator('#post-txn-message')).toContainText('503');
  await page.getByRole('button', { name: 'Post Transaction', exact: true }).click();
  await expect(page.locator('#post-txn-message')).toHaveText('Posted.');
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[0]).toBe(keys[1]);
});

test('missing deployment configuration offers demo without silently fabricating live records', async ({ page }) => {
  await page.route('**/portal/session', route => route.fulfill({ json: { authenticated: false, configured: false } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Sign in to your workspace' })).toBeVisible();
  await expect(page.locator('#login-error')).toContainText('needs operator configuration');
  await expect(page.getByRole('link', { name: 'Explore the synthetic demo' })).toBeVisible();
  await expect(page.locator('#overview-journal-body')).toHaveCount(0);
});

test('copilot errors are shown and a subsequent request can recover', async ({ page }) => {
  await page.goto('/?demo=1');
  await expect(page.locator('#overview-journal-body')).toBeVisible();
  await page.getByRole('button', { name: /04 Copilot/ }).click();
  await page.evaluate(() => { window.Api.askCopilot = async () => { throw new Error('Copilot is unavailable'); }; });
  await page.locator('#copilot-input').fill('Explain this exception');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(page.locator('#copilot-log')).toContainText('Copilot is unavailable');
  await page.evaluate(() => { window.Api.askCopilot = async () => ({ answer: 'Recovered', sources: [{ source_type: 'POLICY_DOC', source_ref: '<img src=x onerror="window.injected=true">' }] }); });
  await page.locator('#copilot-input').fill('Try again');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(page.locator('#copilot-log')).toContainText('Recovered');
  expect(await page.evaluate(() => 'injected' in window)).toBe(false);
});
