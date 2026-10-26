# Development and releases

## Fast local checks

Python 3.9+ and its standard library are sufficient:

```sh
python3 tools/test.py
python3 tools/check_repo.py
python3 tools/package_app.py roadster_sandbox
python3 tools/install_badge.py plan --package dist/roadster_sandbox.zip
python3 tools/export_models.py
```

The host suite exercises physical response, controls, geometry, contacts,
render scheduling and tooling. Hardware modules are isolated in host fixtures;
these checks do not prove that the firmware imports or renders an app.
`tools/test.py` writes an app-hash-stamped result to `test-results/host/summary.json`.
It works when invoked from outside the checkout as well.

## Native preview

Follow [preview setup](../playground/README.md), then run `npm test` in
`playground/`. It starts a fresh staging server and uses the real native renderer.
Inputs and IMU samples are synthetic. Examine both Python state and rendered
frames; never substitute a screenshot-only test for behavior or a stub renderer
for native pico3d. Results and evidence stay under `playground/test-results/`.

Use `PW_PORT=5187 npm test` if the normal preview port is occupied. Only use
`PW_REUSE=1` after deliberately restaging the current source yourself. Tests
always require the pico3d profile; a skipped native suite is not a pass.

Avoid broad refactors inside contact/terrain queries, the fixed-step integrator,
HUD painting and fleet scheduling without profiling. Precomputed geometry,
bounding-volume filters, caches and reduced drawing are deliberate. Preserve
collision bodies even when a distant vehicle mesh is culled.

## Reuse a model

```sh
python3 tools/export_models.py --model roadster_2026
python3 tools/export_models.py --output dist/my-models
```

Unknown model IDs are rejected before any files are written;
the complete set is recorded in `dist/models/manifest.json`. Exports include
triangulated OBJ, flat-color MTL, per-part metadata, pivots, dimensions and
source hashes. No browser or badge is involved. The Python model modules remain
the editable source of truth. Coordinates are metres: +X left, +Y up, +Z front.

## Share a source release

1. Run the host suite, repository audit and native checks appropriate to changes.
2. Package the app and validate the offline installation plan.
3. Update [validation](VALIDATION.md) with the exact source hashes and checks
   for this release. Keep earlier results out of the current release receipt.
   Never label an untested build hardware-verified.
4. Run `python3 tools/package_repo.py`. It creates `dist/roadster-badge-source.zip`
   and `dist/SHA256SUMS`; generated dependencies and backups are excluded.
5. Extract that ZIP into a new folder and rerun host tests, app packaging and
   the offline installation plan. A source release must work on its own.
6. For hardware-affecting changes, obtain the user's device authorization,
   install with the local tool, then check launch, memory, input combinations,
   repeated takeoff/landing, flips and all three scenes. Retain a local receipt.

No command publishes a GitHub repository or release automatically. A source ZIP
contains source files, without a `.git` directory, branch, remote or development
history. To publish an extracted source release:

```sh
git init -b main
git add .
git commit -m "Initial Roadster Badge release"
git remote add origin YOUR_REPOSITORY_URL
git push -u origin main
```

If this folder is already a Git checkout, use its existing `main` branch and
skip initialization and the initial commit. Attach the app ZIP, source ZIP and
checksums to a release if desired. Do not attach `backups/`, `.cache/`,
`node_modules/`, or the generated browser runtime. See [provenance](PROVENANCE.md)
for platform licensing boundaries.

CI runs host tests, the repository audit, app packaging and an offline install
plan on Linux, macOS and Windows. Native preview testing is an explicit separate
workflow because it builds the platform toolchain; physical checks remain manual.
