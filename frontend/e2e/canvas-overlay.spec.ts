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
  test('right-edge vertical tool rack clears the bottom page-controls bar', async ({ page }) => {
    const opened = await openFirstWorksheet(page);
    test.skip(!opened, 'No project/sheet available in this environment.');

    // The rack and the page-controls bar only render once a detection result is
    // loaded, which can take several seconds. Wait for the rack button.
    const boxTrace = page.getByRole('button', { name: /box trace/i });
    const dockReady = await boxTrace
      .first()
      .waitFor({ state: 'visible', timeout: 20_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!dockReady, 'Tool rack not rendered (no detection result).');

    // The rack must offer all four tools, including the new Multi-Select.
    await expect(page.getByRole('button', { name: /pan & select/i }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /multi-select/i }).first()).toBeVisible();
    await expect(boxTrace.first()).toBeVisible();
    await expect(page.getByRole('button', { name: /manual pen/i }).first()).toBeVisible();

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
    expect(dockBox, 'tool rack must be visible').not.toBeNull();

    // The two must NOT intersect (they now live in different screen regions).
    const overlap = !(
      dockBox!.x + dockBox!.width <= pageBarBox!.x ||
      pageBarBox!.x + pageBarBox!.width <= dockBox!.x ||
      dockBox!.y + dockBox!.height <= pageBarBox!.y ||
      pageBarBox!.y + pageBarBox!.height <= dockBox!.y
    );
    expect(overlap, 'tool rack overlaps the page-controls bar').toBe(false);

    // The rack should sit in the right half of the canvas (not bottom-center anymore).
    const viewport = page.viewportSize();
    if (viewport) {
      const dockCenterX = dockBox!.x + dockBox!.width / 2;
      expect(dockCenterX, 'tool rack should be anchored to the right side').toBeGreaterThan(
        viewport.width * 0.5,
      );
    }
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

test.describe('Multi-Select marquee', () => {
  test('dragging a marquee selects many runs and offers batch delete', async ({ page }) => {
    const opened = await openFirstWorksheet(page);
    test.skip(!opened, 'No project/sheet available in this environment.');

    const multiBtn = page.getByRole('button', { name: /multi-select/i }).first();
    const ready = await multiBtn
      .waitFor({ state: 'visible', timeout: 20_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!ready, 'Tool rack not rendered (no detection result).');

    await multiBtn.click();
    await page.waitForTimeout(400);

    const overlay = page.locator(OVERLAY_SELECTOR).first();
    const box = await overlay.boundingBox();
    expect(box, 'overlay must be measurable').not.toBeNull();

    // Keep the drag strictly inside the visible viewport: the OSD overlay can be taller
    // than the window, and a pointer that leaves the window never fires mouseup on the SVG.
    const vp = page.viewportSize()!;
    const x0 = Math.max(box!.x + 10, 20);
    const y0 = Math.max(box!.y + 10, 20);
    const x1 = Math.min(box!.x + box!.width - 10, vp.width - 400); // leave room for right panel
    const y1 = Math.min(box!.y + box!.height - 10, vp.height - 200); // leave room for bottom bar

    // Paint a marquee across most of the visible canvas.
    await page.mouse.move(x0, y0);
    await page.mouse.down();
    await page.mouse.move(x0 + 150, y0 + 150, { steps: 5 });
    // The blue marquee rectangle must be visible mid-drag.
    await expect(page.locator(`${OVERLAY_SELECTOR} rect[stroke="#2563EB"]`)).toHaveCount(1);
    await page.mouse.move(x1, y1, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(600);

    // The action popover must report a multi-selection and expose the batch-delete button.
    await expect(page.getByText(/Pipa Terpilih/i).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /Hapus .*Pipa Terpilih/i })).toBeVisible();
  });
});
