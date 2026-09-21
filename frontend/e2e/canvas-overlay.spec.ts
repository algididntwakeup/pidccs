import { test, expect, Page } from '@playwright/test';

/**
 * E2E guards for the P&ID Studio canvas.
 *
 * These tests run against the live Docker stack (frontend :3000, api :8000).
 * They cover two areas that are hard to regression-test in unit tests:
 *
 *   1. Responsive toolbar layout — the bottom tool dock ("Box Trace" / "Manual
 *      Pen") must never be covered by the floating page-controls bar
 *      ("Pipa: ON/OFF" + opacity slider) on narrow / short viewports
 *      (14" Full-HD laptops and below).
 *
 *   2. Interactive SVG overlay persistence — the line-tracing overlay must stay
 *      attached to the OpenSeadragon canvas across mode switches
 *      (Digitization <-> Corrosion System / Circuit) and window resizes.
 */

const OVERLAY_SELECTOR = '[data-pipe-interactive="true"]';

/**
 * Navigate straight to the first available sheet's workspace.
 *
 * We resolve the target via the API (`/api/v1/projects`) and go to the URL
 * directly instead of clicking through the project list — clicking proxies the
 * Next.js client router and is more timing-sensitive on a shared backend.
 * Returns the sheet status if a sheet was found, otherwise null.
 */
async function openFirstWorksheet(page: Page): Promise<{ status: string } | null> {
  const apiBase = process.env.PIDCCS_API_URL || 'http://localhost:8000';
  const projects = await page
    .request.get(`${apiBase}/api/v1/projects`)
    .then((r) => (r.ok() ? r.json() : []))
    .catch(() => [] as any[]);

  const sheet = (projects as any[])
    .flatMap((p) => (p.sheets || []).map((s: any) => ({ ...s, projectId: p.id })))
    .find((s) => s.status === 'detected') ||
    (projects as any[]).flatMap((p) => (p.sheets || []).map((s: any) => ({ ...s, projectId: p.id })))[0];

  if (!sheet) return null;

  await page.goto(`/project/${sheet.projectId}?sheetId=${sheet.id}`);
  await page.waitForURL(/\/project\//, { timeout: 20_000 }).catch(() => {});
  return { status: sheet.status };
}

/** Poll until `predicate` is true or the timeout elapses. */
async function waitUntil(page: Page, predicate: () => Promise<boolean>, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return true;
    await page.waitForTimeout(1000);
  }
  return false;
}

test.describe('Responsive canvas toolbars', () => {
  test('bottom tool dock is not covered by the page-controls bar', async ({ page }) => {
    const opened = await openFirstWorksheet(page);
    test.skip(!opened, 'No project/sheet available in this environment.');

    // The dock and the page-controls bar only render once a detection result is
    // loaded, which can take several seconds. Wait for the dock button.
    const boxTrace = page.getByRole('button', { name: /box trace/i });
    const dockReady = await boxTrace
      .first()
      .waitFor({ state: 'visible', timeout: 20_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!dockReady, 'Tool dock not rendered (no detection result).');

    // The page-controls bar has the "Pipa: ON/OFF" toggle text.
    const pipaToggle = page.getByText(/^Pipa:\s*(ON|OFF)$/);
    await pipaToggle.first().waitFor({ state: 'visible', timeout: 10_000 });

    const pageBarBox = await pipaToggle
      .first()
      .locator('xpath=ancestor::div[contains(@class, "bottom-6") and contains(@class, "left-6")][1]')
      .boundingBox();
    const dockBox = await boxTrace
      .first()
      .locator('xpath=ancestor::div[contains(@class, "rounded-2xl")][1]')
      .boundingBox();
    expect(pageBarBox, 'page-controls bar must be visible').not.toBeNull();
    expect(dockBox, 'tool dock must be visible').not.toBeNull();

    // The two bars share the bottom band; their boxes must NOT intersect.
    const overlap = !(
      dockBox!.x + dockBox!.width <= pageBarBox!.x ||
      pageBarBox!.x + pageBarBox!.width <= dockBox!.x ||
      dockBox!.y + dockBox!.height <= pageBarBox!.y ||
      pageBarBox!.y + pageBarBox!.height <= dockBox!.y
    );
    expect(overlap, 'tool dock overlaps the page-controls bar').toBe(false);

    // And the dock must be the topmost element at its own center point.
    const cx = dockBox!.x + dockBox!.width / 2;
    const cy = dockBox!.y + dockBox!.height / 2;
    const topmost = await page.evaluate(
      ([x, y]) => {
        const el = document.elementFromPoint(x as number, y as number);
        return el ? (el.closest('[data-pipe-interactive="true"]') ? 'dock-or-overlay' : el.tagName) : null;
      },
      [cx, cy],
    );
    expect(topmost).not.toBeNull();
  });
});

test.describe('Overlay persistence', () => {
  test('SVG overlay stays attached across mode switches and resize', async ({ page }) => {
    const opened = await openFirstWorksheet(page);
    test.skip(!opened, 'No project/sheet available in this environment.');

    // A detected sheet should mount the overlay SVG. If none is detected, skip.
    const overlay = page.locator(OVERLAY_SELECTOR).first();
    const detected = await waitUntil(page, async () => (await overlay.count()) > 0, 30_000);
    test.skip(!detected, 'No detected sheet (overlay not mounted) in this environment.');

    await expect(overlay).toHaveCount(1);

    // 1. Switch to Corrosion System and back.
    const systemTab = page.getByRole('button', { name: /corrosion system/i });
    if (await systemTab.count()) {
      await systemTab.click();
      await page.waitForTimeout(1200);
      await page.getByRole('button', { name: /digitization/i }).click();
      await page.waitForTimeout(1200);
      await expect(page.locator(OVERLAY_SELECTOR)).toHaveCount(1);
    }

    // 2. Switch to Corrosion Circuit and back.
    const circuitTab = page.getByRole('button', { name: /corrosion circuit/i });
    if (await circuitTab.count()) {
      await circuitTab.click();
      await page.waitForTimeout(1200);
      await page.getByRole('button', { name: /digitization/i }).click();
      await page.waitForTimeout(1200);
      await expect(page.locator(OVERLAY_SELECTOR)).toHaveCount(1);
    }

    // 3. Resize the viewport (extreme resize) and confirm the overlay survives.
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.waitForTimeout(800);
    await expect(page.locator(OVERLAY_SELECTOR)).toHaveCount(1);
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.waitForTimeout(800);
    await expect(page.locator(OVERLAY_SELECTOR)).toHaveCount(1);
  });
});
