# Contributing

Start with [architecture](docs/ARCHITECTURE.md) and [development](docs/DEVELOPMENT.md).
Explain the player-visible problem, make a focused change and report what you
tested. Include a native screenshot for visible changes and physical timings
for hardware performance claims. Do not infer badge speed from a desktop run.

Keep tests out of app folders. New art should be code-authored or carry a clear
license and provenance record. Preserve licenses when reusing this project.
Do not commit generated dependencies, native binaries, USB backups or private
device details. `python3 tools/check_repo.py` and `python3 tools/package_repo.py`
check the shareable source boundary.

Use an issue or pull request to describe reproduction steps, scene, motion mode,
fan/air state, firmware information and relevant logs. Remove USB serial numbers
and personal paths from logs. A small deterministic fixture is more useful than
a broad claim that the physics feels wrong.

For a release, follow the checklist in [development](docs/DEVELOPMENT.md).
Do not change behavior purely to satisfy an outdated test: first establish the
intended control or physics contract, then update the fixture and explain why.
