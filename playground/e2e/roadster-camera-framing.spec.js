import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-camera-framing');
const exec = (page, code) => page.evaluate(code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const inspect = (page, expression) => page.evaluate(expression => badge.inspect(expression), expression);
const action = (page, name, value) => page.evaluate(({ name, value }) => badge.appAction(name, value), { name, value });
const step = page => page.evaluate(() => badge.step(1, { dt: 0, buttons: [], pad: [], appControls: {} }));
const camera = page => inspect(page, '[scene.camera_fov, scene.camera_distance, scene.camera_side_offset, scene.background.ex-sim.x, scene.background.ey-sim.y, scene.background.ez-sim.z, scene.background.fx, scene.background.fy, scene.background.fz, scene.background.horizon_y]');

test('Tesla Lineup shares the complete Airfield follow camera through altitude, yaw and boost', async ({ page }) => {
  test.skip(process.env.BADGE_RUNTIME !== 'pico3d', 'Requires actual native pico3d');
  test.setTimeout(120000);
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  // Freeze physical travel to compare the actual rendered camera at identical
  // poses, rather than comparing differently spawned cars or hardcoded source.
  await exec(page, 'def _camera_frame():\n    scene.camera_time = -1.\n    scene.draw(sim, feedback, False)\n__import__("u26_runtime").active.update = _camera_frame');
  await mkdir(output, { recursive: true });
  const rows = [];
  for (const quality of ['full', 'badge']) {
    await exec(page, `scene.set_quality('${quality}')`);
    for (const view of ['chase', 'behind']) {
      for (const pose of [
        { name: 'ground', y: .55, q: [1, 0, 0, 0], boost: 0 },
        { name: 'rising-boost-yaw', y: 12, q: [Math.SQRT1_2, 0, Math.SQRT1_2, 0], boost: .8 },
        { name: 'high-inverted', y: 30, q: [0, 0, 0, 1], boost: 0 },
      ]) {
        const samples = [];
        for (const sceneName of ['flat', 'collection']) {
          await action(page, 'scene', sceneName);
          await action(page, 'camera', view);
          await exec(page, `sim.x, sim.y, sim.z = 12., ${pose.y}, -18.\nsim.q = ${JSON.stringify(pose.q)}\nfeedback.intensity = ${pose.boost}\nfeedback.speed_mix = ${pose.boost}`);
          await step(page);
          samples.push(await camera(page));
          if (quality === 'badge' && view === 'chase' && pose.name === 'ground') {
            const png = await page.evaluate(() => badge.snapshot());
            await writeFile(resolve(output, `camera-${sceneName}.png`), Buffer.from(png.split(',')[1], 'base64'));
          }
        }
        expect(samples[1], `${quality}/${view}/${pose.name}: complete eye, orientation and lens`).toEqual(samples[0]);
        expect(samples[0][2]).toBe(view === 'behind' ? 0 : 4.8);
        if (pose.name === 'ground') expect(samples[0][4]).toBeCloseTo(5.6, 6);
        rows.push({ quality, view, pose, airfield: samples[0], lineup: samples[1] });
      }
    }
  }
  await action(page, 'scene', 'ramp');
  await exec(page, 'sim.y = .55; sim.q = (1.,0.,0.,0.)');
  await step(page);
  const ramp = await camera(page);
  expect(await inspect(page, 'scene.camera_view')).toBe('behind');
  expect(ramp[2]).toBe(0);
  expect(ramp[4]).toBeCloseTo(3.6, 6);
  await writeFile(resolve(output, 'camera-lineup-airfield-equivalence.json'), JSON.stringify({
    boundary: 'Actual MicroPython/WASM and native pico3d. Fixed poses isolate visual camera framing from physics. No badge timing claim.',
    rows, ramp,
  }, null, 2) + '\n');
});
