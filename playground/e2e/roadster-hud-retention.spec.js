import { test, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-hud-retention');
const label = process.env.BADGE_PROFILE_LABEL || 'candidate';
// Eight compact top instruments, with separate speed digits. Exact keys keep
// the cache bounded independently of frames, speed values and motion state.
const hudKeys = ['boost', 'fan', 'jets', 'speed0', 'speed1', 'speed2', 'speed_label', 'tilt'].sort();
const center = [0, 0, 16384, 0, 0, 0];
const step = (page, frames, options = {}) => page.evaluate(({ frames, options }) =>
  badge.step(frames, options), { frames, options: {
    dt: 50, buttons: [], pad: [], imu: center, appControls: {}, ...options,
  } });
const action = (page, name, value) => page.evaluate(({ name, value }) =>
  badge.appAction(name, value), { name, value });
const inspect = (page, expression) => page.evaluate(expression => badge.inspect(expression), expression);
const exec = (page, code) => page.evaluate(code =>
  badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);

async function save(page, name, data) {
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  const stagedSourceHashes = Object.fromEntries(manifest.files.filter(row =>
    row.path.startsWith('/system/apps/roadster_sandbox/')).map(row => [row.path, row.sha256]));
  await writeFile(`${output}/airborne-${label}-${name}.json`, JSON.stringify({
    boundary: 'Actual MicroPython/WASM and native graphics, with synthetic logical inputs and IMU. The stationary y=1000m fixture isolates sustained airborne work from contact costs. WASM ticks_us is a virtual clock, so zero component durations are expected; only call counts and deterministic state are used here. Heap samples are diagnostic because conservative WASM stack retention can vary between calls. These are not physical badge timings or responsiveness measurements. Explicit GC occurs only between blocks.',
    stagedSourceHashes, ...data,
  }, null, 2) + '\n');
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/airborne-${label}-${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
}

test('retained HUD does no region repaint for unchanged pixels and redraws only changed instruments', async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d profile');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await action(page, 'scene', 'flat');
  await step(page, 2);
  await action(page, 'center');
  await step(page, 24);
  expect(await inspect(page, 'motion_input.phase')).toBe('ready');
  await exec(page, `
_hud_original_region = hud._region
_hud_repaints = []
_hud_preserve = True
_hud_fixture_state = {'fan_on': True, 'motion': motion_input.model,
                      'level': {'level_state': 'off', 'level_strength': 0.}}
def _hud_count_region(name, value, bounds, preserve):
    painted = _hud_original_region(name, value, bounds, preserve)
    if painted:
        _hud_repaints.append(name)
    return painted
hud._region = _hud_count_region
sim.fan_force = 18000.
feedback.speed_mph = 100.2 / 1.609344
feedback.run_state, feedback.run_time = 'running', 1.
def _hud_cache_frame():
    hud.draw(sim, feedback, {}, _hud_fixture_state, _hud_preserve)
hud.invalidate()
__import__('u26_runtime').active.update = _hud_cache_frame
`);
  const frame = () => step(page, 1, { dt: 0 });
  const pixels = () => page.locator('canvas').evaluate(canvas =>
    Array.from(canvas.getContext('2d').getImageData(0, 0, 320, 240).data));
  const cases = [];
  await frame();
  expect((await inspect(page, '_hud_repaints')).sort()).toEqual(hudKeys);
  expect(await inspect(page, 'sorted(hud._regions)')).toEqual(hudKeys);
  const fixtures = [
    { name: 'unchanged', code: '', repaints: [] },
    { name: 'ones-digit', code: 'feedback.speed_mph = 101.2 / 1.609344', repaints: ['speed2'] },
    { name: 'unshown-suction', code: 'sim.fan_force = 19000.', repaints: [] },
    { name: 'unshown-launch-timer', code: 'feedback.run_time = 1.25', repaints: [] },
    { name: 'fan-off', code: "_hud_fixture_state['fan_on'] = False", repaints: ['fan'] },
    { name: 'boost-meter', code: 'feedback.rear_fraction = .5', repaints: ['boost'] },
    { name: 'tilt-marker', code: "_hud_fixture_state['motion']['angle_x'] = 14.", repaints: ['tilt'] },
    { name: 'unchanged-again', code: '', repaints: [] },
  ];
  for (const fixture of fixtures) {
    await exec(page, `_hud_repaints = []\n${fixture.code}`);
    await frame();
    const repaints = await inspect(page, '_hud_repaints');
    expect(repaints, fixture.name).toEqual(fixture.repaints);
    const retained = await pixels();
    await exec(page, '_hud_preserve = False');
    await frame();
    const full = await pixels();
    expect(retained, fixture.name).toEqual(full);
    await exec(page, '_hud_preserve = True');
    cases.push({ name: fixture.name, repaints, pixelDifferences: 0 });
  }
  await save(page, 'hud-cache-work', { keys: hudKeys, cases,
    fixture: 'Physics and input are intentionally frozen. Actual native HUD rendering is compared retained versus full after each independent displayed-value change. Region calls are counted without substituting drawing functions.' });
});
