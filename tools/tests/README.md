# Host tests

Run every host check from the repository root:

```sh
python3 tools/test.py
```

The runner needs only Python's standard library and writes a fresh summary to
`test-results/host/summary.json`. To focus on one area:

```sh
python3 tools/test.py --pattern 'test_roadster_flick*.py'
```

These tests cover the portable code directly:

- **Physics and collisions:** rigid-body integration, fan/track contacts, parked
  vehicle contact boxes, repeat launches, fixed-step determinism, 40 Hz badge
  scheduling and conservative collision fast paths.
- **Controls:** independent touch/clicky controls, bounded rotation, airborne tilt,
  center leveling, deliberate flick thresholds, completed directional flips,
  cancellation, sensor stalls and the IMU register transaction contract.
- **Driving and tricks:** real nozzle force/torque and air use, rear-boost launch
  time, smooth held-lift hover/release, hop/kickflip/backflip/spin behavior and
  speed/stunt telemetry.
- **Graphics data:** finite indexed geometry, winding, silhouette landmarks,
  distinct fleet meshes, scenery geometry, camera transforms, model scheduling
  and compact HUD cache/tilt/jet drawing contracts.
- **Tooling:** app-only deterministic packaging, unsafe-path rejection and the
  installer's fake-transport safety cases.

The removed Full Loop remains an internal curved-track physics fixture. Tests
that use it do not expose it as a playable scene. Public scene selection is
checked separately in the browser.

`fixtures/jet_map_oracle.py` is a small original, uncached force-based instrument
painter. It allows the optimized app painter to be checked independently, on a
recording screen here and on the real native renderer in the browser. It is not
an archived application. The suite has no dependencies on other app folders,
previous release ZIPs, private evidence or a connected device.

Real-symlink checks explicitly skip when the operating system or account cannot
create symlinks (for example, Windows without Developer Mode). Other filesystem
errors remain failures. The runner records each skipped test and its reason.

Host success does not establish native rendering or badge performance. Run the
browser suite as described in [playground/README.md](../../playground/README.md)
for real MicroPython/WASM and PicoVector/pico3d validation. Hardware frame rate,
physical touch input, sensor feel and long-session stability require a badge.
