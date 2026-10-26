# Install on your GitHub Universe badge

The game is an app folder, not replacement firmware. The installer in this
repository transfers it over USB, verifies every file, and returns to the
badge's existing menu. It does not change the boot program or other apps.

## What you need

- A **GitHub Universe 2026 Badger 2350**, running its compatible MicroPython
  firmware with `badgeware` and native `pico3d` already installed.
- A USB data cable and a computer with Python 3.9 or newer.
- This repository, or the repository's tools plus its app-only ZIP.

No Node, browser simulator, firmware image, external deployment repository, or
`mpremote` installation is needed. The host USB driver package `pyserial` is the
only Python dependency. The game itself uses the libraries on the badge.

From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 tools/install_badge.py plan
python3 tools/install_badge.py list
```

On Windows, use `py -3` in place of `python3` and activate with
`.venv\Scripts\activate` in Command Prompt. On Linux, your account needs access
to the serial device; use your distribution's normal serial-access group or
udev configuration if it reports permission denied.

`plan` checks the source files and prints their destination, sizes and hashes.
It is entirely offline. `list` reads the operating system's USB serial metadata
without opening a serial connection. It lists all serial devices so you can
identify the GitHub badge by its product name.

## Install

Use the exact port printed by `list`. Examples of port names are
`/dev/cu.usbmodem…` on macOS, `/dev/ttyACM0` on Linux, and `COM7` on Windows.
Replace `YOUR_PORT` below; it is not a literal device name.

```sh
python3 tools/install_badge.py install --port YOUR_PORT
```

That command installs `apps/roadster_sandbox/` from this checkout, saves the old
app locally under `backups/`, verifies the installed bytes and unchanged boot
hashes, then soft-resets into the ordinary badge menu. Select **Roadster Sandbox**
there. If your firmware displays folder names, look for `roadster_sandbox`.

To install the packaged release instead of editable source:

```sh
python3 tools/install_badge.py plan --package dist/roadster_sandbox.zip
python3 tools/install_badge.py install --port YOUR_PORT --package dist/roadster_sandbox.zip
```

To open the game immediately after installation:

```sh
python3 tools/install_badge.py install --port YOUR_PORT --launch game
```

Immediate launch includes a fresh soft reset and a short wait to clear old
Python modules. It runs the game for this session; it does not set an autostart
or alter the normal boot menu. A successful serial launch acknowledgement is
reported as **requested**, not as proof that the display rendered correctly.
Check the badge screen. `--launch none` leaves the interpreter at its REPL for
development work.

Close other serial monitors before installing. The installer interrupts the
current app, so finish your current play session first. Keep the USB cable
connected until verification finishes. The existing firmware must already be
working; a UF2 bootloader drive is not an app-install destination.

## Backups and recovery

Every install makes a new local backup before changing the badge. The printed
directory contains:

- `app/`: the previous Roadster app's files, if it was installed.
- `backup.json`: the previous file hashes and boot hashes.
- `receipt.json`: the target preflight, requested file hashes, verification,
  rollback status and launch request.

These are app backups, not whole-device dumps. The installer does not read
secrets, other apps' contents or factory backup files. Keep this directory until
you are happy with the update. Use `--backup-dir PATH` to choose a new local
directory; the installer refuses to overwrite an existing one.

Restore a previous app with the exact backup directory printed during install:

```sh
python3 tools/install_badge.py restore --port YOUR_PORT --backup backups/YOUR_BACKUP
```

Restore verifies the local backup hashes, saves the current app in another new
backup, then installs the old app. A backup from the first installation is empty;
restoring it removes only `roadster_sandbox` and returns to the menu.

On a normal transfer or readback error, the installer attempts to restore the
previous app automatically. If USB disconnects or power fails, it cannot promise
automatic rollback. Reconnect the badge, run `list` again, then retry the install
or restore from the saved local backup. Do not delete that backup. The receipt
reports whether rollback completed or needs recovery.

## Supported firmware contract

The installer deliberately rejects Tufty boards and generic Pico firmware, even
if they use RP2350. It requires an explicit port every time and never falls back
to another connected device. Some Windows USB drivers show only “USB Serial
Device” instead of a product name. An explicitly selected Raspberry Pi USB
device may reach read-only preflight in that case, but its board identity must
identify Universe/Badger 2350 before any file is changed. Positive identity in
USB or firmware alone is insufficient: preflight also checks
MicroPython/RP2350, `badgeware`, `pico3d`, logical button and launcher APIs,
the menu folder and the system mount layout.

The supported Universe firmware provides:

- Apps in `/system/apps/<name>/`, with `__init__.py` as the entry.
- `badgeware.launch`, `badgeware.reset`, the run loop, and the firmware's logical
  controls and display APIs. The launcher creates the app's injected globals.
  When those globals are absent at an idle REPL, preflight inspects the frozen
  Badge and Controls definitions without constructing them or accessing sensors.
- A read-only FAT `/system` mount backed by `rp2.Flash`, with injected
  `USER_FLASH_SIZE` and `LFS_SIZE` partition sizes and a separate writable root.
- `vfs`, `hashlib`, `binascii`, and enough free space for a single staged file.

Each file is staged and hashed separately on the writable root, then copied to
a temporary file beside its destination. After verification, the old and new
files are renamed into place. The original `/system` filesystem object is
restored read-only in a `finally` block after each mutation. This avoids staging
the entire app on the small writable partition. It is **not** a power-loss-proof
transaction across the whole app; the local backup is the recovery mechanism.

Only `/system/apps/roadster_sandbox/` and a uniquely named temporary transfer
file on `/` are writable targets. Existing boot files are hashed before and
after installation and are never rewritten. Unsafe ZIP paths, symlinks,
duplicate entries, secret filenames and oversized files are rejected.

If preflight fails, stop and read the reported capability or layout mismatch.
This repository does not flash, patch or supply badge firmware. A successful
offline test validates installer logic; a successful package check validates
bytes; neither establishes compatibility with an untested firmware version.

## Commands at a glance

| Command | Hardware access | Purpose |
| --- | --- | --- |
| `plan [--package ZIP]` | None | Check source/package and show destination/hash plan |
| `list` | OS metadata only | Show serial devices without opening them |
| `install --port PORT [--package ZIP]` | Explicit selected badge | Back up, install, verify and return to menu |
| `restore --port PORT --backup DIR` | Explicit selected badge | Back up current app, restore saved app and return to menu |

`install` and `restore` also accept `--backup-dir PATH` and
`--launch menu|game|none`. Run a command with `--help` for its exact options.
