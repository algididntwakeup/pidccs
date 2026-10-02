import { test, expect, Page } from '@playwright/test';

/**
 * E2E coverage for the dynamic "Line Thickness" and "Dot/Node Size" sliders.
 *
 * The sliders live in two places (floating inspector popup + bottom canvas bar) and
 * drive a session-only Zustand store consumed by `InteractivePipeCanvas`:
 *   - marked runs render their visible stroke at exactly `lineThickness`
 *   - every marked run gets two endpoint dots (first & last vertex) with radius `dotSize`
 *
 * The test also guards keyboard isolation: Arrow keys focused on a slider thumb must
 * resize strokes/dots WITHOUT nudging the selected pipes (the canvas listens on window).
 */

const OVERLAY_SELECTOR = '[data-pipe-interactive="true"]';

interface SheetRef {
  id: string;
  projectId: string;
}

/** Flatten the untrusted `/api/v1/projects` payload into sheet refs (skips unknown shapes). */
function collectSheets(payload: unknown): SheetRef[] {
  const sheets: SheetRef[] = [];
  const projects: unknown[] = Array.isArray(payload) ? payload : [];
  for (const project of projects) {
    if (typeof project !== 'object' || project === null) continue;
    if (!('id' in project) || typeof project.id !== 'string') continue;
    if (!('sheets' in project) || !Array.isArray(project.sheets)) continue;
    const projectSheets: unknown[] = project.sheets;
    for (const sheet of projectSheets) {
      if (typeof sheet !== 'object' || sheet === null) continue;
      if (!('id' in sheet) || typeof sheet.id !== 'string') continue;
      sheets.push({ id: sheet.id, projectId: project.id });
    }
  }
  return sheets;
}

/** True when the untrusted `/result` payload contains at least one marked run. */
function hasMarkedRun(payload: unknown): boolean {
  if (typeof payload !== 'object' || payload === null) return false;
  if (!('runs' in payload) || !Array.isArray(payload.runs)) return false;
  const runs: unknown[] = payload.runs;
  return runs.some(
    (run) => typeof run === 'object' && run !== null && 'marked' in run && run.marked === true,
  );
}

/** Open a sheet that has at least one marked run (otherwise skip). */
async function openSheetWithMarkedRun(page: Page): Promise<boolean> {
  const apiBase = process.env.PIDCCS_API_URL || 'http://localhost:8000';
  const projects: unknown = await page.request
    .get(`${apiBase}/api/v1/projects`)
    .then((r) => (r.ok() ? r.json() : null))
    .catch(() => null);
  for (const sheet of collectSheets(projects).slice(0, 12)) {
    const result: unknown = await page.request
      .get(`${apiBase}/api/v1/projects/${sheet.projectId}/sheets/${sheet.id}/result`)
      .then((r) => (r.ok() ? r.json() : null))
      .catch(() => null);
    if (hasMarkedRun(result)) {
      await page.goto(`/project/${sheet.projectId}/sheet/${sheet.id}`);
      return true;
    }
  }
  return false;
}

test.describe('Dynamic thickness & dot size sliders', () => {
  test('popup and toolbar sliders restyle marked strokes and endpoint dots live', async ({ page }) => {
    const found = await openSheetWithMarkedRun(page);
    test.skip(!found, 'No sheet with marked runs available in this environment.');

    const overlay = page.locator(OVERLAY_SELECTOR).first();
    await overlay.waitFor({ state: 'visible', timeout: 30_000 });

    // Defaults before any interaction: marked runs stroke 8px, endpoint dots r=6.
    await expect(
      page.locator(`${OVERLAY_SELECTOR} g[data-run-marked="true"] polyline[stroke-width="8"]`).first(),
    ).toBeAttached();
    await expect(
      page.locator(`${OVERLAY_SELECTOR} g[data-run-marked="true"] circle[r="6"]`).first(),
    ).toBeAttached();

    // Open the action popup by selecting runs with the marquee tool (proven pattern).
    const multiBtn = page.getByRole('button', { name: /multi-select/i }).first();
    await multiBtn.waitFor({ state: 'visible', timeout: 20_000 });
    await multiBtn.click();
    await page.waitForTimeout(300);

    const box = await overlay.boundingBox();
    expect(box).not.toBeNull();
    const vp = page.viewportSize()!;
    const x0 = Math.max(box!.x + 10, 20);
    const y0 = Math.max(box!.y + 10, 20);
    const x1 = Math.min(box!.x + box!.width - 10, vp.width - 400);
    const y1 = Math.min(box!.y + box!.height - 10, vp.height - 200);
    await page.mouse.move(x0, y0);
    await page.mouse.down();
    await page.mouse.move(x0 + 150, y0 + 150, { steps: 5 });
    await page.mouse.move(x1, y1, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(500);

    // The popup slider thumbs only exist while the action popup is open.
    const popup = page.locator('div.absolute.z-40').filter({ hasText: 'Gaya Garis' }).first();
    const thickness = popup.getByRole('slider', { name: 'Line Thickness' });
    const dotSlider = popup.getByRole('slider', { name: 'Dot/Node Size' });
    await expect(thickness).toBeVisible();
    await expect(dotSlider).toBeVisible();

    // Snapshot every selected run's points. Keyboard slider control must change the
    // stroke size without nudging the selected pipes (guard in the slider wrapper).
    const collectSelectedPoints = () =>
      page.$$eval(
        `${OVERLAY_SELECTOR} g:has(polyline[stroke="#FBBF24"])`,
        (groups) =>
          groups.map((g) =>
            Array.from(g.querySelectorAll('polyline')).map((p) => p.getAttribute('points')),
          ),
      );
    const pointsBefore = await collectSelectedPoints();
    expect(pointsBefore.length).toBeGreaterThan(0);

    // Popup 8 -> 10, then bottom bar 10 -> 11; marked strokes follow instantly.
    await thickness.press('ArrowRight');
    await thickness.press('ArrowRight');
    await expect(
      page.locator(`${OVERLAY_SELECTOR} g[data-run-marked="true"] polyline[stroke-width="10"]`).first(),
    ).toBeAttached();
    await page.getByRole('slider', { name: 'Line Thickness' }).last().press('ArrowRight');
    await expect(
      page.locator(`${OVERLAY_SELECTOR} g[data-run-marked="true"] polyline[stroke-width="11"]`).first(),
    ).toBeAttached();
    expect(await collectSelectedPoints()).toEqual(pointsBefore);

    // Dot slider 6 -> 8 (popup) -> 9 (bottom bar); two endpoint dots per marked run.
    await dotSlider.press('ArrowRight');
    await dotSlider.press('ArrowRight');
    await expect(
      page.locator(`${OVERLAY_SELECTOR} g[data-run-marked="true"] circle[r="8"]`).first(),
    ).toBeAttached();
    await page.getByRole('slider', { name: 'Dot/Node Size' }).last().press('ArrowRight');
    await expect(
      page.locator(`${OVERLAY_SELECTOR} g[data-run-marked="true"] circle[r="9"]`).first(),
    ).toBeAttached();
  });
});
