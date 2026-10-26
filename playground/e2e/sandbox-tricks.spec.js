import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/sandbox-tricks');
const lift = [0,0,0,0,1,1,1,1,0,0];
async function save(name, data, page) {
  await mkdir(output, { recursive: true });
  await writeFile(`${output}/${name}.json`, JSON.stringify(data, null, 2) + '\n');
  const png = await page.evaluate(() => badge.snapshot());
  await writeFile(`${output}/${name}.png`, Buffer.from(png.split(',')[1], 'base64'));
}
test.beforeEach(async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires native pico3d');
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await page.evaluate(() => badge.appAction('scene', 'flat'));
});

test('repeated jumps explain weak pressure and refill allows lift without reset', async ({ page }) => {
  // Repeated short burns consume the actual reservoir. Stop before it is
  // empty, at a measured inability to support weight; regulator behavior is
  // not a fixed percentage of the original full tank.
  const hops = [];
  let reserve;
  for (let jump = 0; jump < 8; jump++) {
    let airborne = false;
    for (let burn = 0; burn < 6; burn++) {
      await page.evaluate(nozzles => badge.step(2, { appControls: { nozzles } }), lift);
      const sample = await page.evaluate(() => badge.inspect('[sim.lift_status()["ratio"],sim.airborne]'));
      reserve = sample[0];
      airborne ||= sample[1];
      if (reserve < .8) break;
    }
    await page.evaluate(() => badge.step(2, { appControls: {} }));
    let settled = false;
    for (let fall = 0; fall < 8; fall++) {
      await page.evaluate(() => badge.step(30));
      settled = await page.evaluate(() => badge.inspect('sim.contact_count == 4 and not sim.airborne and abs(sim.vy) < .05 and sim.basis()[1][1] > .99'));
      if (settled) break;
    }
    expect(settled).toBe(true);
    hops.push({ airborne, state: await page.evaluate(() => badge.inspect('[sim.time,sim.air_fraction,sim.lift_status()["ratio"]]')) });
    if (reserve < .8) break;
  }
  expect(hops.length).toBeGreaterThan(1);
  expect(hops.filter(hop => hop.airborne).length).toBeGreaterThan(1);
  expect(reserve).toBeLessThan(.8);
  expect(await page.evaluate(() => badge.inspect('sim.air_mass > __import__("vehicle_spec").TANK_RESIDUAL and sim.pressure < __import__("vehicle_spec").REGULATED_PRESSURE'))).toBe(true);
  const beforeAttempt = await page.evaluate(() => badge.inspect('[sim.y,sim.air_fraction,sim.time]'));
  await page.keyboard.down('j');
  const attempted = await page.evaluate(() => badge.step(2));
  const weak = await page.evaluate(() => badge.inspect('[sim.y,sim.air_fraction,sim.nozzle_forces,sim.lift_status()]'));
  await page.keyboard.up('j');
  expect(weak[1]).toBeGreaterThan(0);
  expect(weak[3].reason).toBe('low_pressure');
  expect(weak[2].slice(4,8).every(x => x > 0)).toBe(true);
  expect(weak[0] - beforeAttempt[0]).toBeLessThan(.5);
  expect(attempted.controlStatus.message).toContain('Low pressure');
  await expect(page.locator('#roadster-control-message')).toContainText('T refills air in place');
  const beforeRefill = await page.evaluate(() => badge.inspect('[sim.x,sim.y,sim.z,sim.q,sim.vx,sim.vy,sim.vz,sim.time]'));
  await page.evaluate(() => badge.appAction('refill'));
  expect(await page.evaluate(() => badge.inspect('[sim.x,sim.y,sim.z,sim.q,sim.vx,sim.vy,sim.vz,sim.time]'))).toEqual(beforeRefill);
  expect(await page.evaluate(() => badge.inspect('sim.air_fraction'))).toBe(1);
  await page.keyboard.down('j');
  await page.evaluate(() => badge.step(24));
  await page.keyboard.up('j');
  const relaunched = await page.evaluate(() => badge.inspect('[sim.y,sim.vy,sim.time]'));
  expect(relaunched[0]).toBeGreaterThan(3);
  expect(relaunched[1]).toBeGreaterThan(5);
  expect(relaunched[2]).toBeGreaterThan(beforeAttempt[2]);
  await save('repeat-jump-refill', {hops, beforeAttempt, weak, beforeRefill, relaunched}, page);
});

for (const [name, key] of [['hop','h'], ['kickflip','k'], ['spin','l'], ['backflip','i']]) {
  test(`keyboard ${name} uses physical thrusters and releases without freezing`, async ({ page }) => {
    await page.keyboard.press(key);
    const samples = [];
    let highest = 0;
    let seenActive = false;
    let rotated = false;
    let sideCapture = false;
    const sidePeak = [0, 0];
    for (let i = 0; i < 100; i++) {
      const outcome = await page.evaluate(() => badge.step(2));
      const row = await page.evaluate(() => badge.inspect('[sim.time,sim.y,sim.basis()[1][1],sim.air_fraction,trick.active,trick.rotation,sim.q,sim.nozzle_forces,sim.basis()[2],sim.omega,trick.phase]'));
      samples.push(row);
      sidePeak[0] = Math.max(sidePeak[0], row[7][8]);
      sidePeak[1] = Math.max(sidePeak[1], row[7][9]);
      highest = Math.max(highest, row[1]);
      seenActive ||= Boolean(row[4]);
      if (!sideCapture && name === 'kickflip' && Math.max(row[7][8], row[7][9]) > 100) {
        sideCapture = true;
        await save('kickflip-side-thrust', { sample: row }, page);
      }
      if (!rotated && ['kickflip', 'backflip'].includes(name) && row[2] < -.5) {
        rotated = true;
        await save(`${name}-inverted`, { sample: row }, page);
      }
      if (seenActive && !outcome.controlStatus.trick) break;
    }
    const final = samples.at(-1);
    expect(seenActive).toBe(true);
    expect(final[4]).toBe('');
    expect(final[3]).toBeLessThan(.95);
    expect(highest).toBeGreaterThan(1);
    if (name !== 'hop') {
      expect(final[5]).toBeGreaterThan(6.2);
      expect(final[2]).toBeGreaterThan(.95);
    }
    if (name === 'kickflip') {
      expect(rotated).toBe(true);
      // Side jets must physically fire in both directions, and counter-thrust
      // must leave a controlled finish instead of a continuing tumble.
      expect(Math.min(...sidePeak)).toBeGreaterThan(100);
      expect(Math.max(...final[9].map(Math.abs))).toBeLessThan(.2);
      expect(Math.max(...samples.map(row => Math.abs(Math.atan2(row[8][0], row[8][2]))))).toBeLessThan(.02);
      expect(samples.filter(row => row[10] === 'lift').every(row => row[7][8] + row[7][9] === 0)).toBe(true);
      expect(samples.some(row => row[5] > 5.5 && row[7][9] > 10)).toBe(true);
    }
    if (name === 'backflip') {
      expect(rotated).toBe(true);
      const pitchStart = samples.find(row => Math.abs(row[9][0]) > .5);
      expect(pitchStart[9][0]).toBeLessThan(-.5);
      expect(pitchStart[8][1]).toBeGreaterThan(0);
      expect(samples.some(row => row[7][4] + row[7][5] > row[7][6] + row[7][7] + 100)).toBe(true);
      expect(samples.some(row => row[7][6] + row[7][7] > row[7][4] + row[7][5] + 100)).toBe(true);
      expect(Math.max(...final[9].map(Math.abs))).toBeLessThan(.2);
    }
    await page.evaluate(() => badge.step(20));
    const later = await page.evaluate(() => badge.inspect('[sim.time,sim.y,sim.nozzle_forces]'));
    expect(later[0]).toBeGreaterThan(final[0] + .6);
    expect(later[2]).toEqual(Array(10).fill(0));
    await save(`preset-${name}`, { samples, highest, sidePeak, final, later }, page);
  });
}
