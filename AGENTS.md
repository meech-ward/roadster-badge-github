# Working on Roadster Badge

Read `README.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT.md` and, for preview
work, `playground/README.md` and `playground/CAPABILITIES.md`. The user's task
defines scope. Keep changes small enough to explain and validate.

- Canonical deployable code is `apps/roadster_sandbox/`; `__init__.py` is the
  entry. Use firmware-injected globals, logical buttons and sibling imports.
- Keep game code independent of the preview. No browser/test imports or
  environment switches in the app. Extend the adapter or report an API gap.
- `run(update)` takes a short zero-argument callback. Input belongs to the
  preceding poll; `badge.ticks_delta` is milliseconds. A non-None return exits.
  The preview supports one loop per app, not nested or sequential loops.
- Preserve continuous physics, explicit reset, independent fan/thrust and the
  intentional touch-versus-clicky mapping. See `docs/CONTROLS.md` before edits.
- Hardware performance matters. Do not replace hot-path scalar/inlined code
  with extra objects or per-frame allocation just to shorten it. Measure changes.
- Tests live under `tools/tests/` or `playground/e2e/`, outside the deployable app.
  Run appropriate host checks and actual MicroPython/WASM checks. A CPython pass
  does not establish native rendering or hardware compatibility.
- Python preview edits require `npm run prepare-runtime` and a new Boot.
  Automated test startup stages source by default; source hashes cannot detect
  an edit that was never staged. Do not silently reuse an old dev server.
- `python3 tools/package_app.py roadster_sandbox` builds an app-only ZIP without
  deploying. `tools/package_repo.py` creates a curated source archive. Never add
  caches, dependencies, backups, serial IDs or personal paths to a release.
- Do not access a physical badge, deploy, flash firmware, read secrets or change
  boot files without the user's authorization. Use the local installer with an
  explicit port. No unrelated device or external checkout is a fallback.
- Keep licenses and provenance truthful. Do not import vehicle code, parameter
  tables or assets from another simulator. If a new third-party dependency is
  needed, document its source and terms before shipping it. Platform libraries
  remain separately licensed dependencies.
- Report host, native preview, package and physical badge checks separately.
  Update `docs/VALIDATION.md` when claims or measured evidence change.

There are no required sibling repositories, personal tools or hidden setup
steps. If you find one, remove that coupling and add a portable check.
