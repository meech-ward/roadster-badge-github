import { test, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const output = resolve(process.env.BADGE_EVIDENCE_DIR || 'test-results/evidence/roadster-scene-readability');
const label = process.env.BADGE_PROFILE_LABEL || 'candidate';
const exec = (page, code) => page.evaluate(code => badge.exec(`exec(${JSON.stringify(code)}, u26_runtime.app.__dict__)`), code);
const inspect = (page, code) => page.evaluate(code => badge.inspect(code), code);
const action = (page, name, value) => page.evaluate(({ name, value }) => badge.appAction(name, value), { name, value });
const step = page => page.evaluate(() => badge.step(1, { dt: 0, buttons: [], pad: [], appControls: {} }));
const hudPixels = page => page.locator('canvas').evaluate(canvas =>
  Array.from(canvas.getContext('2d').getImageData(0, 0, 320, 50).data));

test('badge scenery stays grounded, bounded and clear of the HUD in every scene', async ({ page }) => {
  test.setTimeout(120000);
  await page.goto('/?test=1&app=roadster_sandbox');
  await page.waitForFunction(() => window.badgeReady);
  await page.evaluate(() => badge.boot('roadster_sandbox'));
  await action(page, 'badge');
  await action(page, 'scene', 'flat');
  await step(page);
  // Freeze input/physics only. Scene rendering, clipping, matrix transforms,
  // native geometry submission and drawing all run through the real runtime.
  await exec(page, `
def _readability_frame():
    scene.camera_time = -1.
    scene.draw(sim, feedback, False, viewport=WORLD_VIEWPORT)
__import__('u26_runtime').active.update = _readability_frame
`);
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(resolve('public/generated/index.json'), 'utf8'));
  const rows = [];
  for (const sceneName of ['flat', 'ramp', 'collection']) {
    await action(page, 'scene', sceneName);
    const poses = [['ground', .55], ['air', 30]];
    if (sceneName === 'flat') poses.push(['offsite-ground', .55], ['offsite-air', 30]);
    for (const [pose, height] of poses) {
      await exec(page, `sim.y = ${height}\nsim.vx = sim.vy = sim.vz = 0.`);
      if (pose.startsWith('offsite-')) await exec(page, 'sim.x = sim.z = 400.');
      const before = await inspect(page, '[sim.x, sim.y, sim.z, sim.q, sim.time]');
      const top = await hudPixels(page);
      await step(page);
      expect(await inspect(page, '[sim.x, sim.y, sim.z, sim.q, sim.time]')).toEqual(before);
      expect(await hudPixels(page), `${sceneName}/${pose}: world clip preserves top instruments`).toEqual(top);
      const data = await inspect(page, `{
        'quality': scene.quality, 'triangles': scene.triangle_count,
        'triangleCapacity': scene.scene_triangle_capacity,
        'vertexCapacity': scene.scene_vertex_capacity, 'arenaEstimate': scene.scene_arena_estimate,
        'horizon': scene.background.horizon_y, 'gridLines': scene.background.grid_lines,
        'gridSpacing': scene.background.grid_spacing,
        'camera': [scene.background.ex, scene.background.ey, scene.background.ez,
                   scene.background.fx, scene.background.fy, scene.background.fz],
        'car': [sim.x, sim.y, sim.z, sim.q],
        'obstacles': len(sim.obstacles)
      }`);
      expect(data.quality).toBe('badge');
      expect(data.triangles).toBeGreaterThan(0);
      expect(data.triangles).toBeLessThanOrEqual(data.triangleCapacity);
      expect(data.triangleCapacity).toBeLessThanOrEqual(sceneName === 'collection' ? 3100 : 2700);
      expect(data.vertexCapacity).toBeLessThan(4800);
      expect(data.arenaEstimate).toBeLessThan(sceneName === 'collection' ? 120000 : 100000);
      expect(data.horizon).toBeGreaterThan(-120);
      expect(data.horizon).toBeLessThan(170);
      expect(data.gridLines).toBeLessThanOrEqual(24);
      if (pose.endsWith('air')) expect(data.gridLines).toBeGreaterThan(0);
      expect(data.obstacles > 0).toBe(sceneName === 'collection');
      const image = await page.evaluate(() => badge.snapshot());
      await writeFile(resolve(output, `${label}-${sceneName}-${pose}.png`), Buffer.from(image.split(',')[1], 'base64'));
      rows.push({ scene: sceneName, pose, ...data });
    }
  }
  // The recycled terrain must move by its two-tile period. A one-tile shift
  // swaps both colors at an unchanged world point and visibly makes land slide.
  await action(page, 'scene', 'flat');
  await exec(page, 'sim.y = .55; sim.z = 400.');
  const terrain = [];
  for (const x of [400, 432, 464]) {
    await exec(page, `sim.x = ${x}`);
    await step(page);
    terrain.push(await inspect(page, '[scene._ground_x, scene._ground_z, id(scene.ground), id(scene.geometry)]'));
  }
  expect(terrain.map(row => row[0])).toEqual([384, 448, 448]);
  expect(new Set(terrain.map(row => row[2])).size).toBe(1);
  expect(new Set(terrain.map(row => row[3])).size).toBe(1);
  const margins = [];
  // Keeping at least one full tile on every side avoids a visible patch edge
  // when crossing a positive or negative recycle boundary in any direction.
  for (const axis of ['x', 'z']) {
    for (const value of [-480.01, -480, -479.99, -416.01, -416, -415.99,
                        415.99, 416, 416.01, 479.99, 480, 480.01]) {
      await exec(page, `sim.x = sim.z = 400.\nsim.${axis} = ${value}`);
      await step(page);
      const center = await inspect(page, '[scene._ground_x, scene._ground_z]');
      const x = axis === 'x' ? value : 400;
      const z = axis === 'z' ? value : 400;
      const margin = Math.min(64 - Math.abs(x - center[0]), 64 - Math.abs(z - center[1]));
      expect(margin, `${axis}=${value}: local terrain extends beyond the car`).toBeGreaterThanOrEqual(32 - .0001);
      expect(center.every(coordinate => coordinate % 64 === 0)).toBe(true);
      margins.push({ axis, value, center, margin });
    }
  }
  await writeFile(resolve(output, `${label}.json`), JSON.stringify({
    boundary: 'Actual MicroPython/WASM and native pico3d. Fixed poses isolate environment readability and scene-cache bounds; physics and HUD are preserved. Screenshots require visual review. No physical badge timing, panel readability or sensor claim.',
    sourceHashes: manifest.files.filter(row => row.path.startsWith('/system/apps/roadster_sandbox/')),
    rows, terrain, margins,
  }, null, 2) + '\n');
});
