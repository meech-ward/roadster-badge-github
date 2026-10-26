#!/usr/bin/env python3
"""Plan, install, and restore only Roadster's app folder on a Universe badge.

`plan` is offline. Hardware commands always require an explicit serial port.
No firmware flashing, boot overrides, sibling repositories, or mpremote needed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import time
import uuid
import zipfile

from badge_transport import RawRepl, TransportError
from check_app import AppError, PRIVATE_KEY_MARKER, SAFE_COMPONENT, collect_app, is_secret_name

ROOT = Path(__file__).resolve().parents[1]
SLUG = "roadster_sandbox"
APP = "/system/apps/" + SLUG
MAX_FILE = 512 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_FILES = 256
BOOT_PATHS = ("/boot.py", "/main.py", "/system/boot.py", "/system/main.py")


class InstallError(RuntimeError):
    """An app or a target failed the install contract."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name:
        raise InstallError("Unsafe app path: %r" % name)
    parts = name.split("/")
    if any(not SAFE_COMPONENT.fullmatch(part) or is_secret_name(part) for part in parts):
        raise InstallError("Unsafe or private app path: %r" % name)
    if PurePosixPath(name).is_absolute():
        raise InstallError("Absolute app path is forbidden")
    return name


def validate_files(files: dict[str, bytes], require_entry: bool = True) -> dict[str, bytes]:
    if require_entry and "__init__.py" not in files:
        raise InstallError("The app needs roadster_sandbox/__init__.py")
    if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL:
        raise InstallError("App exceeds the supported file count or total size")
    for name, content in files.items():
        safe_relative(name)
        if len(content) > MAX_FILE:
            raise InstallError("%s exceeds the 512 KiB single-file transfer limit" % name)
        if PRIVATE_KEY_MARKER.search(content):
            raise InstallError("Private key material is not allowed in an app")
    names = set(files)
    for name in names:
        if any(parent.as_posix() in names for parent in PurePosixPath(name).parents if parent.as_posix() != "."):
            raise InstallError("A path cannot be both an app file and a directory")
    return files


def load_package(path: Path) -> dict[str, bytes]:
    """Validate before reading; never extract ZIP paths to the host filesystem."""
    files = {}
    seen = set()
    total = 0
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = member.filename.rstrip("/")
            if name in seen:
                raise InstallError("Duplicate ZIP entry: " + name)
            seen.add(name)
            if not name.startswith(SLUG + "/") and name != SLUG:
                raise InstallError("ZIP must contain only the roadster_sandbox folder")
            relative = name[len(SLUG) + 1:]
            if relative:
                safe_relative(relative)
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise InstallError("ZIP links and special files are forbidden")
            if member.is_dir():
                continue
            if not relative or member.flag_bits & 1:
                raise InstallError("Invalid or encrypted ZIP entry")
            total += member.file_size
            if member.file_size > MAX_FILE or total > MAX_TOTAL or len(files) >= MAX_FILES:
                raise InstallError("ZIP exceeds the supported unpacked size")
            files[relative] = archive.read(member)
    return validate_files(files)


def load_source() -> dict[str, bytes]:
    return validate_files(dict(collect_app(SLUG, ROOT).files))


def hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {name: digest(data) for name, data in sorted(files.items())}


def available_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except ImportError as error:
        raise InstallError("Install USB support: python3 -m pip install -r requirements.txt") from error
    return [{"port": item.device, "product": item.product or item.description or "",
             "manufacturer": item.manufacturer or "", "serial": item.serial_number,
             "vid": item.vid, "pid": item.pid} for item in list_ports.comports()]


def universe_identity(description: str) -> bool:
    name = description.lower()
    return "universe" in name or ("badger" in name and "2350" in name) or ("github" in name and "badg" in name)


def selected_port(port: str, ports: list[dict]) -> dict:
    matches = [item for item in ports if item["port"] == port]
    if len(matches) != 1:
        raise InstallError("Selected serial port is absent; run the list command again")
    metadata = matches[0]
    product = metadata["product"].lower()
    # Windows commonly reports only "USB Serial Device (COM...)". A selected
    # Raspberry Pi USB device may reach read-only preflight; the firmware must
    # then provide the missing positive Universe identity before any write.
    if "tufty" in product or not (universe_identity(product) or metadata.get("vid") == 0x2E8A):
        raise InstallError("Selected USB product is not a supported GitHub Universe badge: " + metadata["product"])
    return metadata


# Executed only after explicit port selection. Each mutation restores the exact
# original /system filesystem object as a read-only mount in a finally block.
# The VfsFat/rp2.Flash layout is a firmware contract, checked before every write.
REMOTE_HELPERS = r'''
import os, sys, json, hashlib, binascii, vfs, rp2, builtins
def _rs_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            data = f.read(1024)
            if not data: break
            h.update(data)
    return binascii.hexlify(h.digest()).decode()
def _rs_exists(path):
    try: os.stat(path); return True
    except OSError as error:
        if error.args[0] == 2: return False
        raise
def _rs_mkdirs(path):
    part = ''
    for name in path.split('/')[1:]:
        part += '/' + name
        if not _rs_exists(part): os.mkdir(part)
def _rs_path(name):
    assert name and '\\' not in name
    for part in name.split('/'):
        assert part not in ('', '.', '..') and not part.startswith('.')
        assert all(c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.' for c in part)
        lower = part.lower()
        assert lower.split('.')[0] not in ('secret', 'secrets', 'credentials', 'credential', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519')
        assert not lower.endswith(('.key', '.p12', '.pfx'))
    return '/system/apps/roadster_sandbox/' + name
def _rs_mutate(operation, *args):
    mounts = [item[0] for item in vfs.mount() if item[1] == '/system']
    assert len(mounts) == 1, 'Expected one system mount'
    saved = mounts[0]
    detached = False
    writable = False
    try:
        vfs.umount('/system')
        detached = True
        vfs.mount(vfs.VfsFat(rp2.Flash(start=0, len=USER_FLASH_SIZE - LFS_SIZE)), '/system')
        writable = True
        operation(*args)
        if hasattr(os, 'sync'): os.sync()
    finally:
        if detached:
            if writable: vfs.umount('/system')
            vfs.mount(saved, '/system', readonly=True)
def _rs_commit(name, stage, expected, token):
    target = _rs_path(name)
    assert stage == '/roadster-transfer-' + token + '.tmp'
    assert _rs_hash(stage) == expected, 'Transfer hash mismatch'
    _rs_mkdirs(target.rsplit('/', 1)[0])
    incoming, previous = target + '.incoming-' + token, target + '.previous-' + token
    assert not _rs_exists(incoming) and not _rs_exists(previous), 'Staging collision'
    moved_previous = False
    try:
        with open(stage, 'rb') as source:
            with open(incoming, 'wb') as destination:
                while True:
                    data = source.read(1024)
                    if not data: break
                    destination.write(data)
        assert _rs_hash(incoming) == expected, 'System staging hash mismatch'
        if _rs_exists(target):
            os.rename(target, previous)
            moved_previous = True
        os.rename(incoming, target)
        assert _rs_hash(target) == expected, 'Installed hash mismatch'
        if moved_previous: os.remove(previous)
    except BaseException:
        if moved_previous and _rs_exists(previous):
            if _rs_exists(target): os.remove(target)
            os.rename(previous, target)
        raise
    finally:
        if _rs_exists(incoming): os.remove(incoming)
def _rs_remove(name):
    os.remove(_rs_path(name))
def _rs_prune(path='/system/apps/roadster_sandbox'):
    assert path == '/system/apps/roadster_sandbox' or path.startswith('/system/apps/roadster_sandbox/')
    if not _rs_exists(path): return
    for name in os.listdir(path):
        child = path + '/' + name
        if os.stat(child)[0] & 0x4000: _rs_prune(child)
    if not os.listdir(path): os.rmdir(path)
'''


REMOTE_PREFLIGHT = r'''
import badgeware, pico3d, picovector
def _rs_export(name):
    return globals().get(name, getattr(badgeware, name, getattr(builtins, name, None)))
_rs_missing = []
for name in ('launch', 'reset'):
    if not callable(getattr(badgeware, name, None)): _rs_missing.append('badgeware.' + name)
if not callable(_rs_export('run')) and not callable(getattr(badgeware, '_run', None)):
    _rs_missing.append('badgeware._run')
_rs_missing += ['pico3d.' + name for name in ('engine', 'mesh', 'scene', 'surface', 'material', 'mat4', 'vec3') if not hasattr(pico3d, name)]
_rs_missing += ['picovector.' + name for name in ('font', 'rect', 'color', 'image') if not hasattr(picovector, name)]
_rs_badge = _rs_export('badge')
if _rs_badge is not None:
    _rs_context = 'active_runtime'
    _rs_required = ('screen', 'BUTTON_LEFT', 'BUTTON_RIGHT', 'BUTTON_UP', 'BUTTON_DOWN',
                    'BUTTON_SELECT', 'BUTTON_BACK', 'BUTTON_MENU', 'BUTTON_HOME', 'HIRES', 'VSYNC')
    _rs_missing += [name for name in _rs_required if _rs_export(name) is None]
    _rs_missing += ['badge.' + name for name in ('mode', 'buttons', 'pad', 'pressed', 'ticks_delta', 'default_clear',
        'imu', 'autorotate', 'rotate_180', 'imu_upright', '_internal_i2c') if not hasattr(_rs_badge, name)]
    _rs_bus = getattr(_rs_badge, '_internal_i2c', None)
    _rs_missing += ['IMU bus.' + name for name in ('readfrom_mem', 'writeto_mem') if not callable(getattr(_rs_bus, name, None))]
else:
    # Universe's launcher creates runtime globals for an app and removes them
    # on exit. At an idle REPL, inspect the firmware definitions without
    # constructing a Badge, configuring its IMU, or entering another app.
    _rs_context = 'launcher_definitions'
    _rs_module = None
    for name in ('.frozen/badgeware/badge', 'badgeware/badge', 'badgeware.badge'):
        if name in sys.modules:
            _rs_module = sys.modules[name]
            break
    _rs_class = getattr(_rs_module, 'Badge', None)
    _rs_controls = getattr(_rs_module, 'Controls', None)
    for name in ('mode', 'poll', 'pressed', 'held', 'released', 'ticks_delta', 'imu',
                 'autorotate', 'rotate_180', 'imu_upright', '_start_internal_i2c'):
        if not hasattr(_rs_class, name): _rs_missing.append('Badge.' + name)
    for name in ('pressed', 'held', 'released'):
        if not callable(getattr(_rs_controls, name, None)): _rs_missing.append('Controls.' + name)
    for name in ('LEFT', 'RIGHT', 'UP', 'DOWN', 'SELECT', 'BACK', 'MENU', 'HOME'):
        if not hasattr(_rs_module, name + '_BUTTON'): _rs_missing.append(name + '_BUTTON')
    for name in ('CAP1208', 'LSM6DS3', 'IMU_ADDRESS'):
        if not hasattr(_rs_module, name): _rs_missing.append('badge module.' + name)
print(json.dumps({'machine': os.uname().machine, 'implementation': sys.implementation.name,
    'missing': _rs_missing, 'runtime_context': _rs_context, 'user_flash': USER_FLASH_SIZE, 'lfs': LFS_SIZE,
    'system_mounts': len([x for x in vfs.mount() if x[1] == '/system']),
    'menu': _rs_exists('/system/apps/menu'), 'temporary_free': os.statvfs('/')[0] * os.statvfs('/')[3],
    'system_free': os.statvfs('/system')[0] * os.statvfs('/system')[3]}))
'''


class BadgeDevice:
    """Path-restricted operations; not a general filesystem/firmware utility."""
    def __init__(self, transport, metadata: dict):
        self.transport = transport
        self.metadata = metadata
        self.token = uuid.uuid4().hex
        self.stage = "/roadster-transfer-" + self.token + ".tmp"

    def _json(self, code: str):
        return json.loads(self.transport.execute(code).strip())

    def preflight(self) -> dict:
        self.transport.execute(REMOTE_HELPERS)
        state = self._json(REMOTE_PREFLIGHT)
        identity = universe_identity(self.metadata.get("product", "")) or universe_identity(state["machine"])
        if (not identity or state["implementation"] != "micropython" or "rp2350" not in state["machine"].lower()
                or "tufty" in state["machine"].lower() or state["missing"] or state["system_mounts"] != 1
                or not state["menu"] or not 0 < state["lfs"] < state["user_flash"] <= 64 * 1024 * 1024):
            raise InstallError("Badge firmware does not satisfy the supported Universe contract: " + json.dumps(state))
        return {"usb": self.metadata, "firmware": state}

    def boot_hashes(self) -> dict:
        return self._json("print(json.dumps({p: _rs_hash(p) if _rs_exists(p) else None for p in %r}))" % (BOOT_PATHS,))

    def inventory(self) -> dict:
        result = self._json("""_rs_inventory = {}
def _rs_visit(path, prefix=''):
    if not _rs_exists(path): return
    for name in os.listdir(path):
        full, relative = path + '/' + name, prefix + name
        _rs_path(relative)
        st = os.stat(full)
        if st[0] & 0x4000: _rs_visit(full, relative + '/')
        else:
            assert len(_rs_inventory) < 256 and st[6] <= 524288, 'Existing app exceeds backup limits'
            _rs_inventory[relative] = {'size': st[6], 'sha256': _rs_hash(full)}
_rs_visit('/system/apps/roadster_sandbox')
print(json.dumps(_rs_inventory))
""")
        for name in result:
            safe_relative(name)
        if sum(item["size"] for item in result.values()) > MAX_TOTAL:
            raise InstallError("Existing app exceeds the backup size limit")
        return result

    def read(self, name: str) -> bytes:
        path = APP + "/" + safe_relative(name)
        output = self.transport.execute("""with open(%r, 'rb') as _rs_file:
    while True:
        _rs_data = _rs_file.read(1536)
        if not _rs_data: break
        print(binascii.b2a_base64(_rs_data).decode().strip())
""" % path)
        return base64.b64decode("".join(output.splitlines()), validate=True)

    def write(self, name: str, content: bytes):
        safe_relative(name)
        self.transport.execute("assert not _rs_exists(%r), 'Transfer file already exists'\nopen(%r, 'wb').close()" % (self.stage, self.stage))
        try:
            for offset in range(0, len(content), 1536):
                encoded = base64.b64encode(content[offset:offset + 1536]).decode("ascii")
                self.transport.execute("with open(%r, 'ab') as _rs_output:\n    _rs_output.write(binascii.a2b_base64(%r))" % (self.stage, encoded))
            self.transport.execute("_rs_mutate(_rs_commit, %r, %r, %r, %r)" % (name, self.stage, digest(content), self.token))
        finally:
            self.cleanup()

    def remove(self, name: str):
        self.transport.execute("_rs_mutate(_rs_remove, %r)" % safe_relative(name))

    def prune(self):
        self.transport.execute("_rs_mutate(_rs_prune)")

    def cleanup(self):
        self.transport.execute("if _rs_exists(%r): os.remove(%r)" % (self.stage, self.stage))

    def launch(self, mode: str):
        if mode == "menu":
            self.transport.reset_to_menu()
        elif mode == "game":
            self.transport.launch(APP)


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_backup(folder: Path) -> dict[str, bytes]:
    if folder.is_symlink():
        raise InstallError("Backup symlinks are forbidden")
    folder = folder.resolve()
    if (folder / "backup.json").is_symlink():
        raise InstallError("Backup symlinks are forbidden")
    record = json.loads((folder / "backup.json").read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("app") != SLUG or record.get("schema") != 1:
        raise InstallError("Not a Roadster installer backup")
    if not isinstance(record.get("files"), dict) or len(record["files"]) > MAX_FILES:
        raise InstallError("Invalid backup file manifest")
    files = {}
    total = 0
    for name, expected in record["files"].items():
        safe_relative(name)
        if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise InstallError("Invalid backup file hash: " + name)
        path = folder / "app" / name
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(folder)):
            raise InstallError("Backup symlinks are forbidden")
        size = path.stat().st_size
        total += size
        if size > MAX_FILE or total > MAX_TOTAL:
            raise InstallError("Backup exceeds the supported file size")
        data = path.read_bytes()
        if digest(data) != expected:
            raise InstallError("Backup hash mismatch: " + name)
        files[name] = data
    return validate_files(files, require_entry=False)


def mirror(device, desired: dict[str, bytes]):
    current = device.inventory()
    changed = []
    # Entry goes last, so an interrupted first install is not menu-visible early.
    for name in sorted(desired, key=lambda name: (name == "__init__.py", name)):
        if current.get(name, {}).get("sha256") != digest(desired[name]):
            device.write(name, desired[name])
            changed.append(name)
    for name in sorted(set(current) - set(desired), reverse=True):
        device.remove(name)
    device.prune()
    actual = {name: item["sha256"] for name, item in device.inventory().items()}
    if actual != hashes(desired):
        raise InstallError("Installed readback does not match the requested app")
    return changed


def install(device, files: dict[str, bytes], output: Path, launch: str = "menu") -> dict:
    """Back up first; restore on an ordinary transfer/verification failure."""
    validate_files(files, require_entry=False)
    preflight = device.preflight()
    boots = device.boot_hashes()
    if not boots.get("/system/main.py"):
        raise InstallError("Expected Universe boot program is absent")
    existing = device.inventory()
    largest = max([len(value) for value in files.values()] + [item["size"] for item in existing.values()] + [0])
    free = preflight["firmware"]
    if free["temporary_free"] < largest + 16 * 1024:
        raise InstallError("Writable temporary filesystem lacks room for one staged file")
    # One verified incoming file coexists with the previous copy on /system.
    # Obsolete files are removed after successful writes, so they cannot be
    # counted as free space during the transfer. Include every positive size
    # delta plus one incoming copy, including space needed for rollback.
    growth = sum(max(0, len(content) - existing.get(name, {}).get("size", 0))
                 for name, content in files.items())
    if free["system_free"] < growth + largest + 16 * 1024:
        raise InstallError("System filesystem lacks room for verified replacement")
    output.mkdir(parents=True, exist_ok=False)
    original = {}
    for name, item in existing.items():
        safe_relative(name)
        content = device.read(name)
        if digest(content) != item["sha256"]:
            raise InstallError("Existing app changed during backup: " + name)
        original[name] = content
        target = output / "app" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    write_json(output / "backup.json", {"schema": 1, "app": SLUG, "files": hashes(original), "boot": boots})
    record = {"schema": 1, "app": SLUG, "preflight": preflight, "boot_before": boots,
              "requested_files": hashes(files), "backup": str(output), "installed": False,
              "launch_requested": "none", "rollback": "not_needed"}
    try:
        record["changed_files"] = mirror(device, files)
        record["boot_after"] = device.boot_hashes()
        if record["boot_after"] != boots:
            raise InstallError("Boot hashes changed; installer will not modify boot files to repair them")
        record["installed"] = True
    except BaseException as error:
        record["error"] = str(error)
        try:
            mirror(device, original)
            record["rollback"] = "restored"
        except BaseException as rollback_error:
            record["rollback"] = "failed"
            record["rollback_error"] = str(rollback_error)
        raise InstallError("Install failed; rollback %s. Backup and receipt: %s. %s" %
                           (record["rollback"], output, error)) from error
    finally:
        try:
            device.cleanup()
        except BaseException as cleanup_error:
            record["cleanup_error"] = str(cleanup_error)
        write_json(output / "receipt.json", record)
    if "cleanup_error" in record:
        raise InstallError("App verified, but temporary-file cleanup failed. See " + str(output / "receipt.json"))
    # A launch problem must not turn a verified installation into a rollback.
    try:
        device.launch(launch)
        record["launch_requested"] = launch
    except BaseException as error:
        record["launch_error"] = str(error)
        raise InstallError("App installed, but launch failed. See " + str(output / "receipt.json")) from error
    finally:
        write_json(output / "receipt.json", record)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List USB serial metadata without opening a device")
    plan = commands.add_parser("plan", help="Validate and show an offline install plan")
    plan.add_argument("--package", type=Path, help="App-only ZIP; defaults to the source checkout")
    for command in ("install", "restore"):
        sub = commands.add_parser(command)
        sub.add_argument("--port", required=True, help="Exact serial port from the list command")
        if command == "install":
            sub.add_argument("--package", type=Path, help="App-only ZIP; defaults to the source checkout")
        else:
            sub.add_argument("--backup", type=Path, required=True, help="Directory containing backup.json")
        sub.add_argument("--backup-dir", type=Path, help="New local backup/receipt directory; must not exist")
        sub.add_argument("--launch", choices=("menu", "game", "none"), default="menu")
    args = parser.parse_args(argv)
    transport = None
    try:
        if args.command == "list":
            print(json.dumps(available_ports(), indent=2))
            return 0
        files = (load_backup(args.backup) if args.command == "restore" else
                 load_package(args.package) if args.package else load_source())
        if args.command == "plan":
            print(json.dumps({"hardware_accessed": False, "destination": APP, "files": hashes(files),
                              "bytes": sum(map(len, files.values())), "file_count": len(files)}, indent=2))
            return 0
        if args.launch == "game" and "__init__.py" not in files:
            raise InstallError("An empty backup removes the app; use --launch menu or none")
        metadata = selected_port(args.port, available_ports())
        output = args.backup_dir or ROOT / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        transport = RawRepl(args.port)
        record = install(BadgeDevice(transport, metadata), files, output, args.launch)
        print("Verified %d app files. Boot files unchanged. Backup/receipt: %s" % (len(files), output))
        if record["launch_requested"] != "none":
            print("Requested %s launch; check the badge display. No boot files were changed." % record["launch_requested"])
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, AppError, InstallError, TransportError) as error:
        print("Cannot complete: " + str(error), file=sys.stderr)
        return 1
    finally:
        if transport is not None:
            transport.close()


if __name__ == "__main__":
    sys.exit(main())
