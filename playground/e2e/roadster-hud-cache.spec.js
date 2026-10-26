import { test, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-hud-cache');
const oracle = resolve('../tools/tests/fixtures/jet_map_oracle.py');
const exec = (page, code) => page.evaluate(code =>
  badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const inspect = (page, expression) => page.evaluate(expression => badge.inspect(expression), expression);
const step = page => page.evaluate(() => badge.step(1, {
  dt: 0, buttons: [], pad: [], imu: [0, 0, 16384, 0, 0, 0], appControls: {},
}));
const pixels = page => page.locator('canvas').evaluate(canvas =>
  Array.from(canvas.getContext('2d').getImageData(271, 0, 49, 50).data));
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

test('jet cache matches an uncached force-based painter at every dot and plume threshold', async ({ page }) => {
  // Read only the exact nozzle-map region asserted below, avoiding 74 full
  // frame transfers while preserving every checked native pixel.
  test.setTimeout(120000);
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d profile');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await page.evaluate(() => badge.appAction('badge'));
  await step(page);
  const source = await readFile(oracle, 'utf8');
  await exec(page, `
_hud_oracle = {'__name__': 'jet_map_oracle'}
exec(${JSON.stringify(source)}, _hud_oracle)
_hud_candidate = hud._jet_map
_hud_map_calls = 0
_hud_use_oracle = False
_hud_telemetry = None
def _count_jet_map(values):
    global _hud_map_calls
    _hud_map_calls += 1
    return _hud_candidate(values)
hud._jet_map = _count_jet_map
hud.invalidate()
def _hud_fixture_update():
    global _hud_telemetry
    if _hud_use_oracle:
        hud._rect(271, 0, 49, 50, hud.INK)
        _colors = {name: getattr(hud, name) for name in ('BODY', 'INK', 'MUTED', 'WHITE', 'DIM', 'GOLD', 'CYAN', 'PINK')}
        _active = _hud_oracle['draw_map'](hud._rect, sim.nozzle_forces, __import__('physics').NOZZLES, _colors)
        hud._rect(271, 49, 49, 1, hud.PANEL)
        _hud_telemetry = {'active_nozzles': _active}
    else:
        _hud_telemetry = hud.draw(sim, feedback, {}, {}, preserve=True)
__import__('u26_runtime').active.update = _hud_fixture_update
`);
  const maxima = await inspect(page, '[n[3] for n in __import__("physics").NOZZLES]');
  const cases = [];
  for (const force of [0, .99999, 1, 1.00001]) {
    cases.push({ name: `dot-${force}`, forces: maxima.map(() => force) });
  }
  for (const fraction of [.20, .21, 1 / 3 - 1e-8, 1 / 3, 1 / 3 + 1e-8,
    2 / 3 - 1e-8, 2 / 3, 2 / 3 + 1e-8, 1 - 1e-8, 1, 1 + 1e-8, 2]) {
    cases.push({ name: `fraction-${fraction}`, forces: maxima.map(maximum => maximum * fraction) });
  }
  for (let index = 0; index < 10; index++) {
    cases.push({ name: `nozzle-${index}-alone`, forces: maxima.map((maximum, i) => i === index ? maximum : 0) });
    cases.push({ name: `nozzle-${index}-below-threshold`, forces: maxima.map((maximum, i) => i === index ? 1 : 0) });
  }
  cases.push({ name: 'all-off-again', forces: maxima.map(() => 0) });
  const records = [];
  let previousCodes = null;
  let savedCalls = 0;
  for (const fixture of cases) {
    await exec(page, `sim.nozzle_forces = ${JSON.stringify(fixture.forces)}\n_hud_use_oracle = False`);
    const callsBefore = await inspect(page, '_hud_map_calls');
    await step(page);
    const actual = await pixels(page);
    const actualTelemetry = await inspect(page, '_hud_telemetry');
    const [codes, callsAfter] = await inspect(page, '[hud._jet_pixels(sim.nozzle_forces), _hud_map_calls]');
    const expectedCall = Number(JSON.stringify(codes) !== JSON.stringify(previousCodes));
    expect(callsAfter - callsBefore, fixture.name).toBe(expectedCall);
    savedCalls += 1 - expectedCall;
    previousCodes = codes;
    await exec(page, '_hud_use_oracle = True');
    await step(page);
    const old = await pixels(page);
    const oldTelemetry = await inspect(page, '_hud_telemetry');
    const differentChannels = actual.reduce((sum, value, i) =>
      sum + Number(value !== old[i]), 0);
    expect(actualTelemetry.active_nozzles, fixture.name).toEqual(oldTelemetry.active_nozzles);
    expect(differentChannels, fixture.name).toBe(0);
    records.push({ ...fixture, codes, mapCalls: callsAfter - callsBefore,
      differentChannels, regionSha256: sha256(Buffer.from(actual)) });
  }
  expect(savedCalls).toBeGreaterThanOrEqual(10);
  await mkdir(output, { recursive: true });
  await writeFile(`${output}/hud-jet-pixel-oracle.json`, JSON.stringify({
    boundary: 'Native PicoVector nozzle-map region x=271..319,y=0..49 from actual MicroPython/WASM. Explicit force fixtures without advancing simulation. The uncached test painter accepts forces directly; retained/full HUD equality is covered separately.',
    oracleSha256: sha256(source), candidateSha256: sha256(await readFile(resolve('../apps/roadster_sandbox/hud.py'))),
    cases: records.length, savedMapCalls: savedCalls, records,
  }, null, 2) + '\n');
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/hud-jet-pixel-oracle.png`, Buffer.from(png.split(',')[1], 'base64'));
});

test('normal-cadence motion tracks neutral-relative tilt through five playing grips', async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d profile');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await exec(page, `
import math
from motion import MotionController
_motion_results = []
for _grip_angle in (0., 25., 45., 70., 85.):
    _grip_r = math.radians(_grip_angle)
    _grip = (0., math.sin(_grip_r), math.cos(_grip_r))
    for _axis in (0, 1):
        for _sign in (-1., 1.):
            _sensor = MotionController(accel_scale=1./8192., gyro_scale=.035)
            _neutral_raw = tuple(round(v*8192.) for v in _grip) + (0,0,0)
            for _ in range(60):
                _sensor.update(_neutral_raw, 20., False)
            _sensor.set_mode('tilt')
            _basis = _sensor.basis_x if _axis == 0 else _sensor.basis_y
            # A held angle has zero angular velocity. The complementary
            # filter must converge on gravity without a phantom flick.
            _r = math.radians(20. * _sign)
            _gravity = tuple(_grip[i]*math.cos(_r) + _basis[i]*math.sin(_r) for i in range(3))
            _raw = tuple(round(v*8192.) for v in _gravity) + (0,0,0)
            for _ in range(150):
                _state = _sensor.update(_raw, 20., True)
            _motion_results.append([_state['calibrated'], _state['tilt_valid'],
                _state['angle_x'] if _axis == 0 else _state['angle_y'],
                _state['tilt_x'] if _axis == 0 else _state['tilt_y'],
                _state['flick_count'], _sign])
`);
  const result = await inspect(page, '_motion_results');
  expect(result).toHaveLength(20);
  for (const [calibrated, valid, angle, tilt, flicks, sign] of result) {
    expect(calibrated).toBe(true);
    expect(valid).toBe(true);
    expect(angle * sign).toBeGreaterThan(19.5);
    expect(angle * sign).toBeLessThan(20.5);
    expect(tilt * sign).toBeGreaterThan(.6);
    expect(flicks).toBe(0);
  }
  await mkdir(output, { recursive: true });
  await writeFile(`${output}/motion-playing-grips.json`, JSON.stringify({
    boundary: 'Real MicroPython/WASM sensor controller with synthetic gravity samples. No physical sensor or timing claim.', result,
  }, null, 2) + '\n');
});
