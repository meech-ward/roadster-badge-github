import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-input-sources');
const OUTER = 'BACK';
const INNER = 'SELECT';
const center = [0, 0, 16384, 0, 0, 0];
const step = (page, frames, options = {}) => page.evaluate(
  ({ frames, options }) => badge.step(frames, options),
  { frames, options: { dt: 25, buttons: [], pad: [], imu: center, appControls: {}, ...options } });
const action = (page, name, value) => page.evaluate(
  ({ name, value }) => badge.appAction(name, value), { name, value });
const read = page => page.evaluate(() => badge.inspect('badge_snapshot()'));

async function tap(page, name, source = 'pad') {
  await step(page, 2, { [source]: [name] });
  await step(page, 2);
}

async function calibrate(page) {
  await action(page, 'center');
  await step(page, 44);
  expect((await read(page)).motion.phase).toBe('ready');
}

async function record(name, page, data) {
  await mkdir(output, { recursive: true });
  await writeFile(`${output}/${name}.json`, JSON.stringify({
    boundary: 'Native MicroPython/WASM and pico3d; logical buttons and IMU are injected. Physical GPIO identity, simultaneous touch, and timing require hardware checks.',
    ...data,
  }, null, 2) + '\n');
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
}

test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires native pico3d profile');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await action(page, 'scene', 'flat');
  await step(page, 2);
});

test('all four clicky directions fire manual jets before motion calibration', async ({ page }) => {
  const samples = [];
  for (const direction of ['LEFT', 'RIGHT', 'UP', 'DOWN']) {
    await step(page, 2);
    await step(page, 2, { buttons: [direction] });
    const snapshot = await read(page);
    expect(snapshot.motion.phase).toBe('center');
    expect(snapshot.motion.model.calibrated).toBe(false);
    expect(snapshot.touch_buttons).toEqual([]);
    expect(snapshot.clicky_buttons).toEqual([direction]);
    expect(snapshot.driving.throttle).toBe(0);
    expect(snapshot.driving.steer).toBe(0);
    const jets = snapshot.driving.nozzles;
    const roll = jets[4] + jets[6] - jets[5] - jets[7];
    const pitch = jets[4] + jets[5] - jets[6] - jets[7];
    if (direction === 'LEFT') { expect(roll).toBeLessThan(0); expect(jets[9]).toBeGreaterThan(0); }
    if (direction === 'RIGHT') { expect(roll).toBeGreaterThan(0); expect(jets[8]).toBeGreaterThan(0); }
    if (direction === 'UP') expect(pitch).toBeGreaterThan(0);
    if (direction === 'DOWN') expect(pitch).toBeLessThan(0);
    samples.push({ direction, snapshot });
  }
  expect(await page.evaluate(() => badge.inspect('trick.active'))).toBe('');
  await record('manual-jets-before-centering', page, { samples });
});

test('touch driving, outer boost and clicky roll and pitch remain independent', async ({ page }) => {
  await calibrate(page);
  await step(page, 3, { pad: ['UP', 'LEFT', OUTER], buttons: ['RIGHT', 'UP'] });
  const combined = await read(page);
  expect(combined.touch_buttons).toEqual(['LEFT', 'UP', 'SELECT']);
  expect(combined.clicky_buttons).toEqual(['RIGHT', 'UP']);
  expect(combined.driving.throttle).toBe(1);
  expect(combined.driving.steer).toBe(1);
  const jets = combined.driving.nozzles;
  expect(jets.slice(0, 4).reduce((a, b) => a + b, 0)).toBeGreaterThan(3);
  expect(jets[4] + jets[6] - jets[5] - jets[7]).toBeGreaterThan(0);
  expect(jets[4] + jets[5] - jets[6] - jets[7]).toBeGreaterThan(0);
  expect(jets[8]).toBeGreaterThan(0);
  await step(page, 2, { buttons: ['LEFT', 'RIGHT', 'UP', 'DOWN'] });
  const opposing = await read(page);
  // Opposite clickies request zero angular rate; real counterjets may still
  // arrest the preceding turn. Their targets, rather than thrust, cancel.
  expect(opposing.rotation.targets).toEqual([0, 0]);
  expect(opposing.driving.nozzles.every(value => value >= 0 && value <= 1)).toBe(true);
  expect(opposing.driving.throttle).toBe(0);
  expect(opposing.driving.steer).toBe(0);
  await record('independent-driving-and-manual-jets', page, { combined, opposing });
});

test('clicky Select toggles fan once per press and never fires rear boost', async ({ page }) => {
  await step(page, 2, { buttons: ['SELECT'] });
  const enabled = await read(page);
  expect(enabled.driving.fan).toBe(1);
  expect(enabled.driving.nozzles).toEqual(Array(10).fill(0));
  await step(page, 12, { buttons: ['SELECT'] });
  expect((await read(page)).driving.fan).toBe(1);
  await step(page, 2);
  await tap(page, 'SELECT', 'buttons');
  const disabled = await read(page);
  expect(disabled.driving.fan).toBe(0);
  expect(disabled.driving.nozzles).toEqual(Array(10).fill(0));
  expect(disabled.motion.phase).toBe('center');
  await record('clicky-select-fan-edge', page, { enabled, disabled });
});

test('calibration and reset require clicky release while touch D-pad keeps driving', async ({ page }) => {
  await action(page, 'center');
  await step(page, 44, { buttons: ['RIGHT', 'UP', 'SELECT'], pad: ['UP'] });
  const calibratedHeld = await read(page);
  expect(calibratedHeld.motion.phase).toBe('ready');
  expect(calibratedHeld.release_pads).toBe(true);
  expect(calibratedHeld.driving.throttle).toBe(1);
  expect(calibratedHeld.driving.nozzles).toEqual(Array(10).fill(0));
  expect(calibratedHeld.driving.fan).toBe(0);
  await step(page, 2, { pad: ['UP'] });
  expect((await read(page)).release_pads).toBe(false);
  await step(page, 2, { buttons: ['RIGHT'], pad: ['UP'] });
  expect((await read(page)).driving.nozzles[8]).toBeGreaterThan(0);
  await action(page, 'reset');
  await step(page, 4, { buttons: ['RIGHT'], pad: ['UP'] });
  const resetHeld = await read(page);
  expect(resetHeld.release_pads).toBe(true);
  expect(resetHeld.driving.throttle).toBe(1);
  expect(resetHeld.driving.nozzles).toEqual(Array(10).fill(0));
  await step(page, 2, { pad: ['UP'] });
  await step(page, 2, { buttons: ['RIGHT'], pad: ['UP'] });
  const resumed = await read(page);
  expect(resumed.release_pads).toBe(false);
  expect(resumed.driving.nozzles[8]).toBeGreaterThan(0);
  await record('clicky-release-guards', page, { calibratedHeld, resetHeld, resumed });
});

test('clicky menu confirmation does not double-toggle fan or leak held jets', async ({ page }) => {
  await calibrate(page);
  await tap(page, 'MENU');
  await tap(page, 'DOWN'); // Reset car is first; Fan is the second option.
  await step(page, 2, { buttons: ['SELECT', 'RIGHT'] });
  await step(page, 8, { buttons: ['SELECT', 'RIGHT'], pad: ['UP'] });
  const guarded = await read(page);
  expect(guarded.menu_open).toBe(false);
  expect(guarded.release_pads).toBe(true);
  expect(guarded.driving.fan).toBe(1);
  expect(guarded.driving.throttle).toBe(1);
  expect(guarded.driving.nozzles).toEqual(Array(10).fill(0));
  await step(page, 2);
  await step(page, 2, { buttons: ['RIGHT'] });
  const resumed = await read(page);
  expect(resumed.driving.fan).toBe(1);
  expect(resumed.driving.nozzles[8]).toBeGreaterThan(0);
  await record('menu-clicky-confirm-release', page, { guarded, resumed });
});

test('Controls help opens from scrolled menu and dismisses safely without pausing physics', async ({ page }) => {
  await calibrate(page);
  await tap(page, 'MENU');
  await tap(page, 'UP', 'buttons');
  const selected = await read(page);
  expect(selected.menu_index).toBe(8);
  await record('controls-menu-row', page, { selected });
  await step(page, 2, { buttons: ['SELECT'] });
  const opened = await read(page);
  expect(opened.menu_open).toBe(false);
  expect(opened.help_open).toBe(true);
  await step(page, 12, { buttons: ['SELECT'] });
  const held = await read(page);
  expect(held.help_open).toBe(true);
  expect(held.sim_time).toBeGreaterThan(opened.sim_time + .25);
  expect(held.driving.fan).toBe(0);
  expect(held.driving.nozzles).toEqual(Array(10).fill(0));
  await record('controls-help', page, { opened, held });
  await step(page, 2);
  await step(page, 2, { pad: [INNER], buttons: ['RIGHT'] });
  await step(page, 8, { pad: [INNER, 'UP'], buttons: ['RIGHT'] });
  const closed = await read(page);
  expect(closed.help_open).toBe(false);
  expect(closed.release_pads).toBe(true);
  expect(closed.driving.throttle).toBe(1);
  expect(closed.driving.nozzles).toEqual(Array(10).fill(0));
  await step(page, 2);
  for (const [source, dismiss] of [['pad', OUTER], ['buttons', 'SELECT'], ['pad', 'MENU']]) {
    await action(page, 'help');
    await step(page, 2);
    await tap(page, dismiss, source);
    const dismissed = await read(page);
    expect(dismissed.help_open).toBe(false);
    expect(dismissed.menu_open).toBe(false);
    expect(dismissed.driving.fan).toBe(0);
    expect(dismissed.driving.nozzles).toEqual(Array(10).fill(0));
  }
  await record('help-dismiss-release', page, { closed });
});
