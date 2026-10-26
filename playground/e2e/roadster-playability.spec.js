import { test, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-playability');
const center = [0, 0, 16384, 0, 0, 0];
const step = (page, frames = 2, options = {}) => page.evaluate(({ frames, options }) => badge.step(frames, options), {
  frames, options: { dt: 25, buttons: [], pad: [], imu: center, appControls: {}, ...options },
});
const action = (page, name, value) => page.evaluate(({ name, value }) => badge.appAction(name, value), { name, value });
const inspect = (page, expression) => page.evaluate(expression => badge.inspect(expression), expression);
const exec = (page, code) => page.evaluate(code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const snapshot = page => inspect(page, `{'app': badge_snapshot(), 'fan': physical_fan,
  'pose': [sim.x, sim.y, sim.z], 'time': sim.time, 'camera': scene.follow_view}`);

async function save(page, name, data) {
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  const frame = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/${name}.png`, Buffer.from(frame.split(',')[1], 'base64'));
  await writeFile(`${output}/${name}.json`, JSON.stringify({
    boundary: 'Actual MicroPython/WASM app and native pico3d; injected logical controls. This checks simultaneous logical edges and held input lifecycle, not physical capacitive behavior or badge timing.',
    sourceHashes: manifest.files.filter(row => row.path.startsWith('/system/apps/roadster_sandbox/')),
    testSha256: createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex'),
    ...data,
  }, null, 2) + '\n');
}

test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires native pico3d profile');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await action(page, 'scene', 'flat');
  await step(page, 3);
});

test('opening MENU and confirm in the same poll never executes an unseen option', async ({ page }) => {
  const trials = [];
  // Reopening always starts on Reset car, even after a different previous row.
  // A simultaneous confirm must never execute that unseen reset.
  for (const row of [0, 1]) {
    for (const confirmSource of ['pad', 'buttons']) {
      await exec(page, `menu_open = False\nmenu_index = ${row}\nphysical_fan = False\nsim.x = 20.\nsim.y = 30.\nsim.vx = sim.vy = sim.vz = 0.`);
      await step(page, 2);
      const before = await snapshot(page);
      const chord = confirmSource === 'pad' ? { pad: ['MENU', 'BACK'] } : { pad: ['MENU'], buttons: ['SELECT'] };
      await step(page, 2, chord);
      const opened = await snapshot(page);
      await step(page, 6, chord);
      const held = await snapshot(page);
      trials.push({ row, confirmSource, before, opened, held });
    }
  }
  // Save every opening chord before checking its guard behavior.
  await save(page, 'menu-open-confirm-chords', { trials });
  for (const { row, confirmSource, before, opened, held } of trials) {
    for (const state of [opened, held]) {
      expect(state.app.menu_open, `row=${row}, source=${confirmSource}`).toBe(true);
      expect(state.app.menu_index).toBe(0);
      expect(state.fan).toBe(false);
      expect(state.pose[0]).toBeCloseTo(before.pose[0], 6);
      expect(state.pose[1]).toBeGreaterThan(20);
      expect(state.time).toBeGreaterThan(before.time);
      expect(state.app.driving.nozzles).toEqual(Array(10).fill(0));
    }
  }
});

test('opening MENU with cancel or navigation first displays Reset car', async ({ page }) => {
  const trials = [];
  for (const extra of ['SELECT', 'UP', 'DOWN']) {
    await exec(page, 'menu_open = False\nmenu_index = 4');
    await step(page, 2);
    await step(page, 2, { pad: ['MENU', extra] });
    const opened = await snapshot(page);
    await step(page, 4, { pad: ['MENU', extra] });
    const held = await snapshot(page);
    trials.push({ extra, opened, held });
  }
  await save(page, 'menu-open-navigation-chords', { trials });
  for (const { extra, opened, held } of trials) {
    for (const state of [opened, held]) {
      expect(state.app.menu_open, extra).toBe(true);
      expect(state.app.menu_index, extra).toBe(0);
      expect(state.app.driving.throttle).toBe(0);
      expect(state.app.driving.nozzles).toEqual(Array(10).fill(0));
    }
  }
});

test('a fresh confirmation after the opening chord works once and requires thrust release', async ({ page }) => {
  // In the startup center prompt, OUTER correctly starts calibration. This
  // case exercises ordinary play after the grip has already been centered.
  await action(page, 'center');
  await step(page, 44);
  expect(await inspect(page, 'motion_input.phase')).toBe('ready');
  await step(page, 2, { pad: ['MENU', 'BACK'], buttons: ['RIGHT'] });
  const chord = await snapshot(page);
  await step(page, 3);
  await step(page, 2, { pad: ['DOWN'] }); // Fan is the second row.
  await step(page, 2);
  expect(await inspect(page, 'MENU_ITEMS[menu_index]')).toBe('Fan');
  await step(page, 2, { pad: ['BACK'], buttons: ['RIGHT'] });
  const confirmed = await snapshot(page);
  await step(page, 8, { pad: ['BACK'], buttons: ['RIGHT'] });
  const held = await snapshot(page);
  await step(page, 2);
  await step(page, 2, { pad: ['BACK'] });
  const repressed = await snapshot(page);
  await save(page, 'menu-fresh-confirmation-and-release', { chord, confirmed, held, repressed });
  expect(chord.app.menu_open).toBe(true);
  for (const state of [confirmed, held]) {
    expect(state.app.menu_open).toBe(false);
    expect(state.fan).toBe(true);
    expect(state.app.release_pads).toBe(true);
    expect(state.app.driving.nozzles).toEqual(Array(10).fill(0));
  }
  expect(repressed.app.release_pads).toBe(false);
  expect(repressed.app.driving.nozzles.slice(0, 4)).toEqual([1, 1, 1, 1]);
  expect(repressed.fan).toBe(true);
});

test('Reset car is the first option on every settings visit and confirmation resets the car', async ({ page }) => {
  await action(page, 'center');
  await step(page, 44);
  expect(await inspect(page, 'MENU_ITEMS[:2]')).toEqual(['Reset car', 'Fan']);
  await exec(page, 'menu_index = 7\nsim.x = 18.\nsim.y = 15.\nsim.z = 25.\nsim.vx = sim.vy = sim.vz = 0.');
  const before = await snapshot(page);
  await step(page, 2, { pad: ['MENU'] });
  await step(page, 2);
  const opened = await snapshot(page);
  expect(opened.app.menu_open).toBe(true);
  expect(opened.app.menu_index).toBe(0);
  expect(opened.pose[0]).toBeCloseTo(18., 6);
  expect(opened.pose[1]).toBeGreaterThan(10.);
  await step(page, 2, { pad: ['BACK'] });
  const reset = await snapshot(page);
  expect(reset.app.menu_open).toBe(false);
  expect(reset.app.release_pads).toBe(true);
  expect(reset.fan).toBe(false); // A default confirm must not toggle Fan.
  expect(Math.abs(reset.pose[0])).toBeLessThan(.01);
  expect(reset.pose[2]).toBeCloseTo(-14., 2);
  expect(reset.pose[1]).toBeLessThan(1.);
  expect(reset.time).toBeLessThan(before.time);
  expect(reset.app.driving.nozzles).toEqual(Array(10).fill(0));
  await step(page, 2);
  await step(page, 2, { pad: ['MENU'] });
  await step(page, 2);
  await step(page, 2, { pad: ['DOWN'] });
  expect(await inspect(page, 'MENU_ITEMS[menu_index]')).toBe('Fan');
  await step(page, 2);
  await step(page, 2, { pad: ['SELECT'] }); // Close without applying Fan.
  await step(page, 2);
  await step(page, 2, { pad: ['MENU'] });
  const reopened = await snapshot(page);
  expect(reopened.app.menu_index).toBe(0);
  expect(reopened.fan).toBe(false);
  await save(page, 'menu-reset-first', { before, opened, reset, reopened });
});
