import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-hud');
const step = (page, frames, options = {}) => page.evaluate(({ frames, options }) =>
  badge.step(frames, options), { frames, options: {
    dt: 25, buttons: [], pad: [], imu: [0, 0, 16384, 0, 0, 0], appControls: {}, ...options,
  }});
const action = (page, action, value) => page.evaluate(({ action, value }) =>
  badge.appAction(action, value), { action, value });
async function record(page, name) {
  const state = await page.evaluate(() => badge.inspect('[badge_snapshot(), hud_state, stunt.snapshot()]'));
  await mkdir(output, { recursive: true });
  await writeFile(`${output}/${name}.json`, JSON.stringify(state, null, 2) + '\n');
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
  return state;
}
test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d profile');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await action(page, 'scene', 'flat');
  await action(page, 'center');
  await step(page, 46);
});

test('native HUD shows actual speed and active boost jets, with a readable control guide', async ({ page }) => {
  const ready = await record(page, 'hud-ready');
  expect(ready[1].world_rect).toEqual([0, 50, 320, 190]);
  expect(ready[1].fan_bounds).toEqual([64, 0, 44, 50]);
  expect(ready[1].tilt_gauge.bounds).toEqual([206, 0, 65, 50]);
  expect(ready[1].hint_lines).toEqual([]);
  await step(page, 30, { pad: ['UP', 'BACK'] });
  const boosted = await record(page, 'hud-boost');
  expect(boosted[1].speed_kmh).toBeGreaterThan(20);
  expect(boosted[0].driving.nozzles.slice(0, 4)).toEqual([1, 1, 1, 1]);
  expect(boosted[1].active_nozzles).toEqual([0, 1, 2, 3]);
  await step(page, 5);
  await action(page, 'help');
  await step(page, 2);
  const help = await record(page, 'controls-guide');
  expect(help[0].help_open).toBe(true);
  const before = help[0].sim_time;
  await step(page, 10);
  expect(await page.evaluate(() => badge.inspect('sim.time'))).toBeGreaterThan(before);
  await step(page, 2, { pad: ['SELECT'] });
  await step(page, 2);
  expect(await page.evaluate(() => badge.inspect('help_open'))).toBe(false);
});

test('a natural jump tracks airtime diagnostically, lands, and can lift again without resetting', async ({ page }) => {
  await step(page, 36, { pad: ['SELECT'] });
  const flight = await record(page, 'hud-airtime');
  expect(flight[2].airborne).toBe(true);
  expect(flight[2].airtime).toBeGreaterThan(.5);
  expect(flight[2].peak_height).toBeGreaterThan(2);
  let landed = false;
  for (let i = 0; i < 60; i++) {
    await step(page, 10);
    const s = await page.evaluate(() => badge.inspect('stunt.snapshot()'));
    if (s.message && !s.airborne) { landed = true; break; }
  }
  expect(landed).toBe(true);
  await record(page, 'hud-landing');
  const before = await page.evaluate(() => badge.inspect('[sim.time, sim.y]'));
  await step(page, 24, { pad: ['SELECT'] });
  const after = await page.evaluate(() => badge.inspect('[sim.time, sim.y, sim.air_fraction]'));
  expect(after[0]).toBeGreaterThan(before[0]);
  expect(after[1]).toBeGreaterThan(before[1] + 1);
  expect(after[2]).toBe(1);
});

test('manual thrusters record an actual airborne roll without a persistent HUD callout', async ({ page }) => {
  await step(page, 36, { pad: ['SELECT'] });
  let seen = false;
  for (let i = 0; i < 35; i++) {
    await step(page, 4, { buttons: ['RIGHT'] });
    const flight = await page.evaluate(() => badge.inspect('stunt.snapshot()'));
    if (flight.rolls >= 1) {
      expect(flight.airborne).toBe(true);
      expect(flight.message).toMatch(/ROLL RIGHT/);
      expect(await page.evaluate(() => badge.inspect('trick.active'))).toBe('');
      await record(page, 'hud-manual-roll');
      seen = true;
      break;
    }
  }
  expect(seen).toBe(true);
});

test('compact native HUD never paints into the world below its top strip', async ({ page }) => {
  await page.evaluate(() => badge.exec(`exec(${JSON.stringify(`
_compact_state = {'motion': motion_input.model, 'level': {'level_state': 'tilt'},
                  'fan_on': True, 'hint': 'MENU: OPTIONS / RELEASE CONTROLS',
                  'inspecting_name': 'Original Roadster'}
_compact_stunt = {'message': 'ROLL RIGHT', 'airborne': True, 'airtime': 2.}
_compact_render = False
feedback.speed_mph = 311. / 1.609344
feedback.rear_fraction = 1.
feedback.rear_force = 44000.
sim.fan_force = 18000.
sim.nozzle_forces = [n[3] for n in __import__('physics').NOZZLES]
motion_input.model['angle_x'] = -28.
motion_input.model['angle_y'] = 28.
def _compact_frame():
    global hud_state
    screen.pen = color.rgb(37, 61, 79)
    screen.clear()
    if _compact_render:
        hud_state = hud.draw(sim, feedback, _compact_stunt, _compact_state, False)
__import__('u26_runtime').active.update = _compact_frame
`)}, u26_runtime.app.__dict__)`));
  await step(page, 1, { dt: 0 });
  const background = await page.locator('canvas').evaluate(canvas =>
    Array.from(canvas.getContext('2d').getImageData(0, 50, 1, 1).data));
  await page.evaluate(() => badge.exec('u26_runtime.app._compact_render = True'));
  await step(page, 1, { dt: 0 });
  const result = await page.locator('canvas').evaluate((canvas, background) => {
    const ctx = canvas.getContext('2d');
    const lower = ctx.getImageData(0, 50, 320, 190).data;
    const changed = Array.from({ length: lower.length / 4 }, (_, i) =>
      background.some((value, channel) => lower[4 * i + channel] !== value)).filter(Boolean).length;
    const upper = ctx.getImageData(0, 0, 320, 50).data;
    const instrumentPixels = Array.from({ length: upper.length / 4 }, (_, i) =>
      background.some((value, channel) => upper[4 * i + channel] !== value)).filter(Boolean).length;
    return { changedWorldPixels: changed, instrumentPixels, background };
  }, background);
  expect(result.changedWorldPixels).toBe(0);
  expect(result.instrumentPixels).toBeGreaterThan(1000);
  const shown = await record(page, 'hud-top-only-all-instruments');
  expect(shown[1].active_nozzles).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  expect(shown[1].fan_on).toBe(true);
  expect(shown[1].hint_lines).toEqual([]);
  await writeFile(`${output}/hud-top-only-pixel-boundary.json`, JSON.stringify(result, null, 2) + '\n');
  for (const status of ['LEVEL', 'WAIT', 'CLIP']) {
    await page.evaluate(status => badge.exec(`exec(${JSON.stringify(`
motion_input.model.update({'calibrated': True, 'armed': True, 'angle_x': 0.,
    'angle_y': 0., 'tilt_x': 0., 'tilt_y': 0., 'flick_active': False,
    'tilt_valid': ${status === 'LEVEL' ? 'True' : 'False'},
    'sensor_clipped': ${status === 'CLIP' ? 'True' : 'False'}})
_compact_state['level'] = {'level_state': 'level', 'level_strength': .75}
`)}, u26_runtime.app.__dict__)`), status);
    await step(page, 1, { dt: 0 });
    const telemetry = await record(page, `hud-top-only-${status.toLowerCase()}`);
    expect(telemetry[1].tilt_gauge.status).toBe(status);
    expect(telemetry[1].tilt_gauge.valid).toBe(status === 'LEVEL');
    expect(telemetry[1].tilt_gauge.marker).toEqual(status === 'LEVEL' ? [0, 0] : null);
    const outside = await page.locator('canvas').evaluate((canvas, background) => {
      const pixels = canvas.getContext('2d').getImageData(0, 50, 320, 190).data;
      return Array.from(pixels).filter((value, i) => value !== background[i % 4]).length;
    }, background);
    expect(outside, status).toBe(0);
  }
});
