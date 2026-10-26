# Native preview tests

Run from `playground/` after installing dependencies and building the native
runtime:

```sh
npm test
```

The test server restages canonical app source before boot. Tests execute the real
MicroPython/WASM interpreter and native pico3d/PicoVector; the controls and IMU
samples are synthetic. These are not physical badge frame-rate or gesture-feel
measurements.

The suite covers:

- Driving, individual nozzles, presets, fan behavior and repeat lift after landing.
- Three-scene browsing, confirmation guards and rejection of the removed loop.
- Airfield/Lineup camera equality, plus direct-behind and ramp cameras.
- All four deliberate flick directions, weaker strokes staying manual, completion,
  held input, cancellation and sensor recovery after slow frames.
- Independent touch/clicky controls, Reset Car first in Settings, and control help.
- Top-only HUD bounds, readable states, exact retained/full redraw equality and
  jet-map equality against a small force-based test painter.
- Fleet inspection/collision behavior, smooth altitude assistance and speed effects.

Screenshots and diagnostic JSON go to `test-results/evidence/`. Playwright's
failure traces remain in `test-results/`. Set `BADGE_EVIDENCE_DIR` to write visual
evidence elsewhere. No prior screenshot archive, external app or old release is
required. Historical pixel expectations for the removed HUD design are not part
of this suite.

Use `npx playwright test --list` for the exact inventory, and pass one spec path
or `--grep` to focus a run. Keep the preview server stopped for normal tests.
`PW_REUSE=1` is only for deliberate reuse after manually restaging app edits.
