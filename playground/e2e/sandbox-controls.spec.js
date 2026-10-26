import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

async function record(name, data) {
  const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/sandbox-controls');
  await mkdir(output, { recursive: true });
  await writeFile(`${output}/${name}.json`, JSON.stringify(data, null, 2)+'\n');
}

const released = { throttle: 0, brake: 0, steer: 0, fan: false, nozzles: Array(10).fill(0) };
const read = page => page.evaluate(() => badge.inspect('controls'));
const step = page => page.evaluate(() => badge.step(1, { dt: 16 }));

test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d profile');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
});

test('sandbox keyboard controls combine driving, fan and all ten independent nozzles', async ({ page }) => {
  await expect(page.locator('#roadster-controls')).toBeVisible();
  await expect(page.locator('#badge-controls')).toBeHidden();
  expect(await read(page)).toEqual(released);
  await page.keyboard.down('w');
  await page.keyboard.down('a');
  await page.keyboard.press('f');
  for (const digit of '1234567890') await page.keyboard.down(digit);
  await step(page);
  expect(await read(page)).toEqual({ throttle: 1, brake: 0, steer: 1, fan: true, nozzles: Array(10).fill(1) });
  expect(await page.evaluate(() => badge.inspect('sim.nozzle_forces'))).toEqual(expect.arrayContaining([expect.any(Number)]));
  await expect(page.locator('#roadster-fan')).toHaveAttribute('aria-pressed', 'true');
  for (const digit of '1234567890') await page.keyboard.up(digit);
  await page.keyboard.up('w');
  await page.keyboard.up('a');
  await page.keyboard.down('ArrowDown');
  await page.keyboard.down('ArrowRight');
  await page.keyboard.down('Space');
  await step(page);
  expect(await read(page)).toEqual({ ...released, throttle: -1, brake: 1, steer: -1, fan: true });
  await page.keyboard.up('ArrowDown');
  await page.keyboard.up('ArrowRight');
  await page.keyboard.up('Space');
  await step(page);
  expect(await read(page)).toEqual({ ...released, fan: true });
});

test('fan ignores repeat; independent key, group and pointer holds release correctly', async ({ page }) => {
  await page.keyboard.down('f');
  await page.keyboard.down('f');
  await page.keyboard.down('f');
  await page.keyboard.up('f');
  // The UI sends a boolean. MicroPython does not treat bool as an int for
  // isinstance, so verify actual suction instead of only the toggle state.
  await page.evaluate(() => badge.step(30));
  const fanPhysics = await page.evaluate(() => badge.inspect('[sim.fan_speed, sim.fan_force]'));
  expect(fanPhysics[0]).toBeGreaterThan(.9);
  expect(fanPhysics[1]).toBeGreaterThan(1000);
  await page.keyboard.down('b');
  const rear = page.getByRole('button', { name: 'Rear thruster 1', exact: true });
  await rear.hover();
  await page.mouse.down();
  await page.keyboard.up('b');
  await step(page);
  expect(await read(page)).toEqual({ ...released, fan: true, nozzles: [1, 0, 0, 0, 0, 0, 0, 0, 0, 0] });
  await page.mouse.move(1, 1);
  await page.mouse.up();
  await page.keyboard.down('q');
  await page.keyboard.down('e');
  await step(page);
  expect((await read(page)).nozzles).toEqual([0, 0, 0, 0, 1, 1, 1, 1, 0, 0]);
  await page.keyboard.up('q');
  await step(page);
  expect((await read(page)).nozzles).toEqual([0, 0, 0, 0, 0, 1, 0, 1, 0, 0]);
  await page.keyboard.up('e');
  await page.keyboard.down('j');
  await step(page);
  expect((await read(page)).nozzles).toEqual([0, 0, 0, 0, 1, 1, 1, 1, 0, 0]);
  await page.keyboard.up('j');
  await step(page);
  expect(await read(page)).toEqual({ ...released, fan: true });
});

test('focus loss releases forces without pausing and typed text cannot drive the car', async ({ page }) => {
  await page.keyboard.press('f');
  await page.keyboard.down('w');
  await page.keyboard.down('j');
  await step(page);
  const before = await page.evaluate(() => badge.inspect('sim.time'));
  await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  await step(page);
  expect(await read(page)).toEqual({ ...released, fan: true });
  expect(await page.evaluate(() => badge.inspect('sim.time'))).toBeGreaterThan(before);
  await page.keyboard.up('w');
  await page.keyboard.up('j');
  const expression = page.locator('#expression');
  await expression.fill('');
  await expression.pressSequentially('wasdfrhklxt1234567890');
  await step(page);
  expect(await read(page)).toEqual({ ...released, fan: true });
  await expect(expression).toHaveValue('wasdfrhklxt1234567890');
  await page.locator('h1').click();
  await page.keyboard.down('w');
  await step(page);
  await expression.focus();
  await page.keyboard.up('w');
  await step(page);
  expect((await read(page)).throttle).toBe(0);
});

test('explicit reset, scene selection and reboot clear held controls', async ({ page }) => {
  await page.keyboard.down('w');
  await page.keyboard.down('1');
  await page.keyboard.press('f');
  await page.evaluate(() => badge.step(12));
  await page.getByRole('button', { name: 'Reset car R', exact: true }).click();
  await step(page);
  expect(await read(page)).toEqual(released);
  expect(await page.evaluate(() => badge.inspect('sim.air_fraction'))).toBe(1);
  await expect(page.locator('#roadster-fan')).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.down('w');
  await page.keyboard.down('1');
  await step(page);
  expect(await read(page)).toEqual(released);
  await page.keyboard.up('w');
  await page.keyboard.up('1');
  await page.locator('#roadster-scene').selectOption('flat');
  await step(page);
  expect(await page.evaluate(() => badge.inspect('sim.scene_name'))).toBe('flat');
  await page.keyboard.down('w');
  await step(page);
  expect((await read(page)).throttle).toBe(1);
  await page.keyboard.up('w');
  await page.locator('#roadster-scene').selectOption('ramp');
  await step(page);
  expect(await page.evaluate(() => badge.inspect('sim.scene_name'))).toBe('ramp');
  await page.locator('h1').click();
  await page.keyboard.press('f');
  await page.keyboard.down('j');
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  expect(await read(page)).toEqual(released);
  await expect(page.locator('#roadster-scene')).toHaveValue('collection');
  await page.keyboard.up('j');
  await expect(page.locator('#roadster-controls')).toBeVisible();
  expect(await read(page)).toEqual(released);
});

test('generic app control adapter supports deterministic step input and actions', async ({ page }) => {
  await page.evaluate(() => badge.appAction('scene', 'flat'));
  await page.evaluate(() => badge.step(4, { dt: 16, appControls: { throttle: .4, brake: .2, steer: -.3, fan: .6, nozzles: [0, 0, 0, 0, .1, .2, .3, .4, .5, .6] } }));
  expect(await read(page)).toEqual({ throttle: .4, brake: .2, steer: -.3, fan: .6, nozzles: [0, 0, 0, 0, .1, .2, .3, .4, .5, .6] });
  await step(page);
  expect(await read(page)).toEqual(released);
  await page.evaluate(() => badge.appAction('reset'));
  expect(await page.evaluate(() => badge.inspect('[sim.scene_name,sim.air_fraction]'))).toEqual(['flat', 1]);
});

test('trick shortcuts preserve driving and fan while manual thrusters take over', async ({ page }) => {
  await page.evaluate(() => badge.appAction('scene', 'flat'));
  await page.keyboard.down('w');
  await page.keyboard.press('f');
  await page.keyboard.press('k');
  const outcome = await step(page);
  expect((await read(page)).throttle).toBe(1);
  expect((await read(page)).fan).toBe(true);
  expect(outcome.controlStatus.trick).toBe('kickflip');
  await expect(page.locator('#roadster-fan')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-trick="kickflip"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#roadster-control-metrics')).toContainText('Air');
  await expect(page.locator('#roadster-control-metrics')).toContainText('Lift');
  await page.keyboard.down('j');
  const manual = await step(page);
  expect(manual.controlStatus.trick).toBeFalsy();
  expect((await read(page)).nozzles).toEqual([0, 0, 0, 0, 1, 1, 1, 1, 0, 0]);
  expect((await read(page)).throttle).toBe(1);
  expect((await read(page)).fan).toBe(true);
  await page.keyboard.up('j');
  await page.keyboard.up('w');
});

test('preset buttons and keyboard repeats dispatch once; cancellation is explicit', async ({ page }) => {
  await page.evaluate(() => {
    window.controlActions = [];
    const original = badge.appAction.bind(badge);
    badge.appAction = (action, value) => { window.controlActions.push([action, value]); return original(action, value); };
  });
  await page.keyboard.down('h');
  await page.keyboard.down('h');
  await page.keyboard.down('h');
  await page.keyboard.up('h');
  expect((await step(page)).controlStatus.trick).toBe('hop');
  expect(await page.evaluate(() => window.controlActions)).toEqual([['trick', 'hop']]);
  await page.keyboard.press('x');
  expect((await step(page)).controlStatus.trick).toBeFalsy();
  await page.getByRole('button', { name: 'Spin L', exact: true }).click();
  expect((await step(page)).controlStatus.trick).toBe('spin');
  await page.getByRole('button', { name: 'Cancel trick X', exact: true }).click();
  expect((await step(page)).controlStatus.trick).toBeFalsy();
  await page.getByRole('button', { name: 'Hop H', exact: true }).click();
  expect((await step(page)).controlStatus.trick).toBe('hop');
  await page.keyboard.press('r');
  expect((await step(page)).controlStatus.trick).toBeFalsy();
  await page.getByRole('button', { name: 'Backflip I', exact: true }).click();
  expect((await step(page)).controlStatus.trick).toBe('backflip');
  await page.keyboard.press('x');
  expect((await step(page)).controlStatus.trick).toBeFalsy();
});

test('refill restores air without resetting the pose or held controls', async ({ page }) => {
  await page.evaluate(() => badge.appAction('scene', 'flat'));
  await page.evaluate(() => badge.step(12, { appControls: { nozzles: [1, 1, 1, 1, 0, 0, 0, 0, 0, 0] } }));
  // ticks_delta belongs to the previous poll; settle that delay before the
  // zero-time action comparison rather than changing runtime clock semantics.
  await page.evaluate(() => badge.step(2, { dt: 0 }));
  const before = await page.evaluate(() => badge.inspect('[sim.x,sim.y,sim.z,sim.q,sim.air_fraction,sim.time]'));
  expect(before[4]).toBeLessThan(1);
  await page.keyboard.down('w');
  await page.keyboard.press('t');
  const outcome = await page.evaluate(() => badge.step(1, { dt: 0 }));
  const after = await page.evaluate(() => badge.inspect('[sim.x,sim.y,sim.z,sim.q,sim.air_fraction,sim.time]'));
  expect(after.slice(0, 4)).toEqual(before.slice(0, 4));
  expect(after[4]).toBe(1);
  expect(after[5]).toBe(before[5]);
  expect(outcome.controlStatus.air_percent).toBe(100);
  expect((await read(page)).throttle).toBe(1);
  await page.keyboard.up('w');
  await page.getByRole('button', { name: 'Refill air T', exact: true }).click();
  await page.evaluate(() => badge.step(1, { dt: 0 }));
  expect(await page.evaluate(() => badge.inspect('sim.air_fraction'))).toBe(1);
});

test('reset explains a still-held J; release and press restores lift after reset or focus loss', async ({ page }) => {
  await page.keyboard.down('j');
  await step(page);
  await page.keyboard.press('r');
  await step(page);
  expect((await read(page)).nozzles).toEqual(Array(10).fill(0));
  await expect(page.locator('#roadster-control-message')).toContainText('Release and press J again');
  await page.keyboard.down('j');
  await step(page);
  expect((await read(page)).nozzles).toEqual(Array(10).fill(0));
  await page.keyboard.up('j');
  await page.keyboard.down('j');
  await step(page);
  expect((await read(page)).nozzles.slice(4, 8)).toEqual([1, 1, 1, 1]);
  await expect(page.locator('#roadster-control-message')).not.toContainText('Release and press');
  await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  await step(page);
  expect((await read(page)).nozzles).toEqual(Array(10).fill(0));
  await page.keyboard.up('j');
  await page.locator('h1').click();
  await page.keyboard.down('j');
  await step(page);
  expect((await read(page)).nozzles.slice(4, 8)).toEqual([1, 1, 1, 1]);
  await page.keyboard.up('j');
});

test('rejected inverted and low-pressure presets preserve the fan toggle and suction', async ({ page }) => {
  // Historical regression fixture: Full Loop is no longer a selectable scene.
  await page.evaluate(() => badge.exec('u26_runtime.app.reset_sim("loop")'));
  await page.keyboard.press('f');
  await page.evaluate(() => badge.step(60));
  await page.evaluate(() => badge.exec('from physics import LOOP_RADIUS, LOOP_Z\ns = u26_runtime.app.sim\ns.y = 2 * LOOP_RADIUS - .5\ns.z = LOOP_Z\ns.q = (0.0, 1.0, 0.0, 0.0)'));
  await page.evaluate(() => badge.step(6));
  const before = await page.evaluate(() => badge.inspect('[sim.x,sim.y,sim.z,sim.q,sim.fan_force]'));
  const rejected = await page.evaluate(() => badge.appAction('trick', 'kickflip'));
  expect(rejected.trick).toBeFalsy();
  expect(rejected.fan_on).toBe(true);
  expect(rejected.message).toContain('upright');
  expect(await page.evaluate(() => badge.inspect('[sim.x,sim.y,sim.z,sim.q,sim.fan_force]'))).toEqual(before);
  await expect(page.locator('#roadster-fan')).toHaveAttribute('aria-pressed', 'true');
  await step(page);
  expect((await read(page)).fan).toBe(true);
  expect(await page.evaluate(() => badge.inspect('sim.fan_force'))).toBeGreaterThan(1000);

  await page.evaluate(() => badge.appAction('scene', 'flat'));
  await page.keyboard.press('f');
  await step(page);
  await page.evaluate(() => badge.exec(`from vehicle_spec import TANK_MASS, TANK_PRESSURE, REGULATED_PRESSURE, AMBIENT_PRESSURE
s = u26_runtime.app.sim
s.air_mass = TANK_MASS * (AMBIENT_PRESSURE + .1 * (REGULATED_PRESSURE - AMBIENT_PRESSURE)) / TANK_PRESSURE`));
  await step(page);
  const supply = await page.evaluate(() => badge.inspect('[sim.lift_status()["reason"],sim.lift_status()["ratio"],sim.pressure < __import__("vehicle_spec").REGULATED_PRESSURE]'));
  expect(supply[0]).toBe('low_pressure');
  expect(supply[1]).toBeLessThan(1);
  expect(supply[2]).toBe(true);
  const beforeLowPressure = await page.evaluate(() => badge.inspect('[sim.x,sim.y,sim.z,sim.q,sim.fan_force]'));
  const lowPressure = await page.evaluate(() => badge.appAction('trick', 'hop'));
  expect(lowPressure.trick).toBeFalsy();
  expect(lowPressure.fan_on).toBe(true);
  expect(lowPressure.message).toContain('air reserve');
  expect(await page.evaluate(() => badge.inspect('[sim.x,sim.y,sim.z,sim.q,sim.fan_force]'))).toEqual(beforeLowPressure);
  await expect(page.locator('#roadster-fan')).toHaveAttribute('aria-pressed', 'true');
  await step(page);
  expect((await read(page)).fan).toBe(true);
});

test('a fan toggle made while a trick action awaits acknowledgement takes precedence', async ({ page }) => {
  await page.evaluate(() => badge.appAction('scene', 'flat'));
  await page.evaluate(() => {
    // Delay the real worker request; the portable action and its returned
    // telemetry still execute in MicroPython, after the newer keyboard input.
    const send = Worker.prototype.postMessage;
    Worker.prototype.postMessage = function(message, ...args) {
      if (message.type === 'appAction' && message.data.action === 'trick') {
        window.trickRequestPending = true;
        setTimeout(() => send.call(this, message, ...args), 250);
      } else send.call(this, message, ...args);
    };
    const action = badge.appAction.bind(badge);
    badge.appAction = (name, value) => {
      const result = action(name, value);
      if (name === 'trick') window.pendingTrick = result;
      return result;
    };
  });
  await page.keyboard.press('k');
  await page.waitForFunction(() => window.trickRequestPending);
  await page.keyboard.press('f');
  const accepted = await page.evaluate(() => window.pendingTrick);
  expect(accepted.trick).toBe('kickflip');
  expect(accepted.fan_on).toBe(false);
  await expect(page.locator('#roadster-fan')).toHaveAttribute('aria-pressed', 'true');
  const outcome = await step(page);
  expect((await read(page)).fan).toBe(true);
  expect(outcome.controlStatus.trick).toBe('kickflip');
});

test('spooled fan and all ten manual jets apply forces together; J lifts when suction is released', async ({ page }) => {
  await page.evaluate(() => badge.appAction('scene', 'flat'));
  await page.keyboard.press('f');
  await page.evaluate(() => badge.step(60));
  for (const digit of '1234567890') await page.keyboard.down(digit);
  await page.evaluate(() => badge.step(3));
  const firing = await page.evaluate(() => badge.inspect('[sim.fan_force,sim.nozzle_forces,sim.air_fraction]'));
  expect(firing[0]).toBeGreaterThan(1000);
  expect(firing[1].every(force => force > 100)).toBe(true);
  expect(firing[2]).toBeLessThan(1);
  expect((await read(page)).fan).toBe(true);
  for (const digit of '1234567890') await page.keyboard.up(digit);
  await page.keyboard.down('j');
  await page.evaluate(() => badge.step(9));
  const held = await page.evaluate(() => badge.inspect('[sim.y,sim.contact_count,sim.nozzle_forces,sim.lift_status()]'));
  expect(held[1]).toBe(4);
  expect(held[2].slice(4,8).every(force => force > 100)).toBe(true);
  expect(held[3].reason).toBe('fan');
  await page.keyboard.press('f');
  await page.evaluate(() => badge.step(36));
  await page.keyboard.up('j');
  const lifted = await page.evaluate(() => badge.inspect('[sim.y,sim.vy,sim.contact_count]'));
  expect(lifted[0]).toBeGreaterThan(held[0]+2);
  expect(lifted[1]).toBeGreaterThan(5);
  expect(lifted[2]).toBe(0);
  await record('native-fan-and-jets', { firing, held, lifted });
});

test('a preset fires against fan suction and continues when the user releases the fan', async ({ page }) => {
  await page.evaluate(() => badge.appAction('scene', 'flat'));
  await page.keyboard.press('f');
  await page.evaluate(() => badge.step(60));
  await page.keyboard.press('k');
  const waiting = await page.evaluate(() => badge.step(12));
  const held = await page.evaluate(() => badge.inspect('[sim.fan_force,sim.nozzle_forces,sim.contact_count,trick.rotation,sim.air_fraction]'));
  expect(waiting.controlStatus.trick).toBe('kickflip');
  expect(waiting.controlStatus.fan_on).toBe(true);
  expect(held[0]).toBeGreaterThan(1000);
  expect(held[1].slice(4,8).every(force => force > 100)).toBe(true);
  expect(held[2]).toBe(4);
  expect(held[3]).toBe(0);
  expect(held[4]).toBeLessThan(.99);
  await page.keyboard.press('f');
  const released = await page.evaluate(() => badge.step(36));
  expect(released.controlStatus.trick).toBe('kickflip');
  expect(released.controlStatus.fan_on).toBe(false);
  expect(await page.evaluate(() => badge.inspect('sim.y'))).toBeGreaterThan(2);
  await page.keyboard.press('f');
  const airborneFan = await step(page);
  expect(airborneFan.controlStatus.trick).toBe('kickflip');
  expect(airborneFan.controlStatus.fan_on).toBe(true);
  await page.keyboard.press('x');
  const cancelled = await step(page);
  expect(cancelled.controlStatus.trick).toBeFalsy();
  expect(cancelled.controlStatus.fan_on).toBe(true);
  await record('native-fan-and-preset', { waiting: waiting.controlStatus, held,
    released: released.controlStatus, airborneFan: airborneFan.controlStatus,
    cancelled: cancelled.controlStatus });
});
