"""Offline installer contracts; fake devices do not establish hardware support."""

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest

from fs_fixtures import make_symlink
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_badge as installer
from badge_transport import RawRepl, TransportError


class FakeDevice:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.boot = {"/system/main.py": "boot-hash"}
        self.operations = []
        self.fail_write_once = None
        self.fail_all_writes = False
        self.corrupt_read = False
        self.corrupt_write_once = None
        self.boot_changes = False
        self.free = 16 * 1024 * 1024
        self.launch_failure = False
        self.cleanup_failure = False

    def preflight(self):
        self.operations.append("preflight")
        return {"firmware": {"temporary_free": self.free, "system_free": self.free}}

    def boot_hashes(self):
        if self.boot_changes and any(isinstance(op, tuple) and op[0] == "write" for op in self.operations):
            return {"/system/main.py": "changed-by-something-else"}
        return dict(self.boot)

    def inventory(self):
        return {name: {"size": len(content), "sha256": installer.digest(content)}
                for name, content in self.files.items()}

    def read(self, name):
        self.operations.append(("read", name))
        return b"changed" if self.corrupt_read else self.files[name]

    def write(self, name, content):
        self.operations.append(("write", name))
        if self.fail_all_writes or self.fail_write_once == name:
            self.fail_write_once = None
            raise OSError("simulated disconnect")
        self.files[name] = content
        if self.corrupt_write_once == name:
            self.files[name] += b"corruption"
            self.corrupt_write_once = None

    def remove(self, name):
        self.operations.append(("remove", name))
        del self.files[name]

    def prune(self):
        self.operations.append("prune")

    def cleanup(self):
        self.operations.append("cleanup")
        if self.cleanup_failure:
            raise OSError("cleanup failed")

    def launch(self, mode):
        self.operations.append(("launch", mode))
        if self.launch_failure:
            raise OSError("launch failed")


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "backup"
        self.original = {"__init__.py": b"old entry", "old.py": b"old helper"}
        self.updated = {"__init__.py": b"new entry", "physics.py": b"new physics"}

    def receipt(self):
        return json.loads((self.output / "receipt.json").read_text())

    def test_install_backs_up_and_verifies_before_launch(self):
        device = FakeDevice(self.original)
        result = installer.install(device, self.updated, self.output)
        self.assertEqual(device.files, self.updated)
        self.assertEqual(installer.load_backup(self.output), self.original)
        self.assertEqual(result["boot_before"], result["boot_after"])
        self.assertEqual(device.operations[-1], ("launch", "menu"))
        writes = [op[1] for op in device.operations if isinstance(op, tuple) and op[0] == "write"]
        self.assertEqual(writes[-1], "__init__.py")

    def test_first_install_has_empty_restorable_backup(self):
        device = FakeDevice()
        installer.install(device, self.updated, self.output, "none")
        self.assertEqual(installer.load_backup(self.output), {})
        installer.install(device, {}, self.root / "undo", "menu")
        self.assertEqual(device.files, {})

    def test_identical_app_does_not_rewrite_flash(self):
        device = FakeDevice(self.updated)
        result = installer.install(device, self.updated, self.output)
        self.assertEqual(result["changed_files"], [])
        self.assertFalse(any(isinstance(op, tuple) and op[0] == "write" for op in device.operations))

    def test_failed_transfer_rolls_back_exact_previous_app(self):
        device = FakeDevice(self.original)
        device.fail_write_once = "__init__.py"
        with self.assertRaisesRegex(installer.InstallError, "rollback restored"):
            installer.install(device, self.updated, self.output)
        self.assertEqual(device.files, self.original)
        self.assertEqual(self.receipt()["rollback"], "restored")
        self.assertNotIn(("launch", "menu"), device.operations)

    def test_corrupt_readback_rolls_back(self):
        device = FakeDevice(self.original)
        device.corrupt_write_once = "physics.py"
        with self.assertRaisesRegex(installer.InstallError, "readback"):
            installer.install(device, self.updated, self.output)
        self.assertEqual(device.files, self.original)

    def test_disconnected_rollback_keeps_backup_and_failure_receipt(self):
        device = FakeDevice(self.original)
        device.fail_all_writes = True
        # Simulate a partially written entry before both transfer and rollback fail.
        original_write = device.write
        original_inventory = device.inventory
        disconnected = [False]
        def fail_after_modification(name, data):
            device.files[name] = b"partial"
            disconnected[0] = True
            original_write(name, data)
        def offline_inventory():
            if disconnected[0]:
                raise OSError("device disconnected")
            return original_inventory()
        device.write = fail_after_modification
        device.inventory = offline_inventory
        with self.assertRaisesRegex(installer.InstallError, "rollback failed"):
            installer.install(device, self.updated, self.output)
        self.assertEqual(installer.load_backup(self.output), self.original)
        self.assertIn("rollback_error", self.receipt())

    def test_backup_failure_never_mutates_badge(self):
        device = FakeDevice(self.original)
        device.corrupt_read = True
        with self.assertRaisesRegex(installer.InstallError, "during backup"):
            installer.install(device, self.updated, self.output)
        self.assertEqual(device.files, self.original)
        self.assertFalse(any(isinstance(op, tuple) and op[0] == "write" for op in device.operations))

    def test_missing_boot_contract_never_writes(self):
        device = FakeDevice(self.original)
        device.boot = {}
        with self.assertRaisesRegex(installer.InstallError, "boot program"):
            installer.install(device, self.updated, self.output)
        self.assertEqual(device.files, self.original)
        self.assertFalse(self.output.exists())

    def test_changed_boot_detected_but_never_overwritten(self):
        device = FakeDevice(self.original)
        device.boot_changes = True
        with self.assertRaisesRegex(installer.InstallError, "Boot hashes changed"):
            installer.install(device, self.updated, self.output)
        self.assertEqual(device.files, self.original)
        self.assertFalse(any(isinstance(op, tuple) and op[0] == "write" and op[1].startswith("/") for op in device.operations))

    def test_low_space_fails_before_backup_or_flash_write(self):
        device = FakeDevice(self.original)
        device.free = 100
        with self.assertRaisesRegex(installer.InstallError, "lacks room"):
            installer.install(device, self.updated, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(device.files, self.original)

    def test_space_preflight_does_not_count_obsolete_files_as_already_deleted(self):
        device = FakeDevice({"__init__.py": b"old", "obsolete.bin": b"x" * 100_000})
        device.free = 150_000
        desired = {"__init__.py": b"new", "first.bin": b"a" * 60_000, "second.bin": b"b" * 60_000}
        with self.assertRaisesRegex(installer.InstallError, "System filesystem lacks room"):
            installer.install(device, desired, self.output)
        self.assertFalse(self.output.exists())

    def test_previous_backup_is_never_overwritten(self):
        self.output.mkdir()
        (self.output / "keep").write_text("safe")
        with self.assertRaises(FileExistsError):
            installer.install(FakeDevice(), self.updated, self.output)
        self.assertEqual((self.output / "keep").read_text(), "safe")

    def test_launch_failure_preserves_successful_install_receipt(self):
        device = FakeDevice(self.original)
        device.launch_failure = True
        with self.assertRaisesRegex(installer.InstallError, "App installed, but launch failed"):
            installer.install(device, self.updated, self.output)
        self.assertEqual(device.files, self.updated)
        self.assertTrue(self.receipt()["installed"])
        self.assertEqual(self.receipt()["rollback"], "not_needed")

    def test_cleanup_failure_is_reported_and_does_not_launch(self):
        device = FakeDevice(self.original)
        device.cleanup_failure = True
        with self.assertRaisesRegex(installer.InstallError, "cleanup failed"):
            installer.install(device, self.updated, self.output)
        self.assertIn("cleanup_error", self.receipt())
        self.assertNotIn(("launch", "menu"), device.operations)

    def test_restore_checks_local_backup_hashes(self):
        installer.install(FakeDevice(self.original), self.updated, self.output)
        (self.output / "app" / "old.py").write_bytes(b"changed")
        with self.assertRaisesRegex(installer.InstallError, "Backup hash mismatch"):
            installer.load_backup(self.output)

    def test_restore_refuses_symlink(self):
        installer.install(FakeDevice(self.original), self.updated, self.output)
        target = self.output / "app" / "old.py"
        target.unlink()
        make_symlink(self, target, self.output / "app" / "__init__.py")
        with self.assertRaisesRegex(installer.InstallError, "symlinks"):
            installer.load_backup(self.output)

    def test_source_paths_cannot_escape_or_read_secrets(self):
        for path in ("../main.py", "/main.py", "a/../../main.py", "a\\main.py", "a//b", ".env", "secrets.py", "folder/id_rsa", "a.key"):
            with self.subTest(path=path), self.assertRaises(installer.InstallError):
                installer.validate_files({"__init__.py": b"", path: b""})

    def test_conflicting_file_directory_paths_rejected(self):
        with self.assertRaisesRegex(installer.InstallError, "both an app file"):
            installer.validate_files({"__init__.py": b"", "data": b"", "data/file": b""})

    def test_private_key_content_rejected(self):
        with self.assertRaisesRegex(installer.InstallError, "Private key"):
            installer.validate_files({"__init__.py": b"-----BEGIN RSA " + b"PRIVATE KEY-----"})

    def package(self, entries):
        path = self.root / "app.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, value in entries:
                archive.writestr(name, value)
        return path

    def test_package_bytes_preserved(self):
        path = self.package([("roadster_sandbox/" + k, v) for k, v in self.updated.items()])
        self.assertEqual(installer.load_package(path), self.updated)

    def test_package_rejects_foreign_root_and_traversal(self):
        for name in ("main.py", "other_app/a.py", "roadster_sandbox/../main.py", "roadster_sandbox//etc", "roadster_sandbox/a\\b"):
            with self.subTest(name=name), self.assertRaises(installer.InstallError):
                installer.load_package(self.package([("roadster_sandbox/__init__.py", b""), (name, b"")]))

    def test_package_rejects_symlink(self):
        entry = zipfile.ZipInfo("roadster_sandbox/link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaisesRegex(installer.InstallError, "links"):
            installer.load_package(self.package([("roadster_sandbox/__init__.py", b""), (entry, b"../main.py")]))

    def test_package_rejects_oversized_file(self):
        with self.assertRaisesRegex(installer.InstallError, "unpacked size"):
            installer.load_package(self.package([("roadster_sandbox/__init__.py", b"x" * (installer.MAX_FILE + 1))]))

    def test_package_rejects_duplicate_entries(self):
        with self.assertWarns(UserWarning):
            path = self.package([("roadster_sandbox/__init__.py", b"first"), ("roadster_sandbox/__init__.py", b"second")])
        with self.assertRaisesRegex(installer.InstallError, "Duplicate"):
            installer.load_package(path)

    def test_explicit_port_is_required_even_if_only_one_device(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            installer.main(["install"])

    def test_wrong_board_rejected_before_open(self):
        for product in ("Tufty 2350 MicroPython", "Pico RP2350", "GitHub Tufty Universe"):
            with self.subTest(product=product), self.assertRaisesRegex(installer.InstallError, "not a supported"):
                installer.selected_port("COM7", [{"port": "COM7", "product": product}])

    def test_explicit_selection_never_falls_back_to_other_board(self):
        ports = [{"port": "COM7", "product": "GitHub Badger2350 MicroPython"},
                 {"port": "COM8", "product": "Tufty 2350"}]
        self.assertEqual(installer.selected_port("COM7", ports), ports[0])
        with self.assertRaises(installer.InstallError):
            installer.selected_port("COM9", ports)

    def test_windows_generic_usb_description_can_reach_firmware_identity_check(self):
        port = {"port": "COM7", "product": "USB Serial Device (COM7)", "vid": 0x2E8A}
        self.assertEqual(installer.selected_port("COM7", [port]), port)

    def test_generic_rp2350_firmware_identity_is_rejected_before_writes(self):
        state = {"implementation": "micropython", "machine": "Raspberry Pi Pico 2 with RP2350", "missing": [],
                 "system_mounts": 1, "menu": True, "lfs": 1024 * 1024, "user_flash": 16 * 1024 * 1024}
        remote = types.SimpleNamespace(execute=lambda code: "")
        device = installer.BadgeDevice(remote, {"product": "USB Serial Device (COM7)", "vid": 0x2E8A})
        device._json = lambda code: state
        with self.assertRaisesRegex(installer.InstallError, "firmware does not satisfy"):
            device.preflight()

    def test_universe_board_identity_accepts_windows_generic_usb_name(self):
        state = {"implementation": "micropython", "machine": "GitHub Badger2350 with RP2350", "missing": [],
                 "system_mounts": 1, "menu": True, "lfs": 1024 * 1024, "user_flash": 16 * 1024 * 1024}
        device = installer.BadgeDevice(types.SimpleNamespace(execute=lambda code: ""),
                                       {"product": "USB Serial Device (COM7)", "vid": 0x2E8A})
        device._json = lambda code: state
        self.assertEqual(device.preflight()["firmware"], state)

    def test_plan_never_discovers_or_opens_hardware(self):
        with patch.object(installer, "load_source", return_value=self.updated), \
                patch.object(installer, "available_ports") as ports, patch.object(installer, "RawRepl") as raw, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(installer.main(["plan"]), 0)
        ports.assert_not_called()
        raw.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())["hardware_accessed"])


class MountCleanupTests(unittest.TestCase):
    def runtime(self, fail_mount=False):
        calls = []
        saved = object()
        def mount(*args, **kwargs):
            if not args:
                return [(saved, "/system")]
            calls.append(("mount", args, kwargs))
            if fail_mount and args[0] != saved:
                raise OSError("could not mount writable")
        vfs = types.SimpleNamespace(mount=mount, umount=lambda path: calls.append(("umount", path)), VfsFat=lambda flash: "writable")
        rp2 = types.SimpleNamespace(Flash=lambda **kwargs: kwargs)
        namespace = {"USER_FLASH_SIZE": 16 * 1024 * 1024, "LFS_SIZE": 1024 * 1024}
        with patch.dict(sys.modules, {"vfs": vfs, "rp2": rp2}):
            exec(installer.REMOTE_HELPERS, namespace)
        return namespace, calls, saved

    def test_system_mount_is_restored_after_operation_exception(self):
        namespace, calls, saved = self.runtime()
        def fail():
            raise ValueError("write failed")
        with self.assertRaisesRegex(ValueError, "write failed"):
            namespace["_rs_mutate"](fail)
        self.assertEqual(calls[-1], ("mount", (saved, "/system"), {"readonly": True}))

    def test_system_mount_is_restored_if_writable_mount_fails(self):
        namespace, calls, saved = self.runtime(fail_mount=True)
        with self.assertRaisesRegex(OSError, "could not mount"):
            namespace["_rs_mutate"](lambda: None)
        self.assertEqual(calls[-1], ("mount", (saved, "/system"), {"readonly": True}))

    def test_device_paths_reject_private_and_escaping_files_before_read(self):
        namespace, _, _ = self.runtime()
        for name in ("../main.py", "secrets.py", "data/.env", "/main.py", "a\\b", "key.pfx"):
            with self.subTest(name=name), self.assertRaises(AssertionError):
                namespace["_rs_path"](name)


class PreflightContractTests(unittest.TestCase):
    """Model the actual launcher's idle REPL without creating hardware objects."""
    def state(self, active=False, missing_control=False):
        def never_call(*args, **kwargs):
            self.fail("Preflight constructed or activated hardware/runtime state")
        class Badge:
            def __new__(cls):
                never_call()
        for name in ('mode', 'poll', 'pressed', 'held', 'released', 'ticks_delta', 'imu',
                     'autorotate', 'rotate_180', 'imu_upright', '_start_internal_i2c'):
            setattr(Badge, name, never_call)
        controls = types.SimpleNamespace(pressed=never_call, held=never_call, released=never_call)
        module = types.SimpleNamespace(Badge=Badge, Controls=controls, CAP1208=never_call,
                                       LSM6DS3=never_call, IMU_ADDRESS=0x6A)
        for name in ('LEFT', 'RIGHT', 'UP', 'DOWN', 'SELECT', 'BACK', 'MENU', 'HOME'):
            setattr(module, name + '_BUTTON', object())
        if missing_control:
            del module.MENU_BUTTON
        badgeware = types.SimpleNamespace(launch=never_call, reset=never_call, _run=never_call)
        pico3d = types.SimpleNamespace(**{name: never_call for name in
                                         ('engine', 'mesh', 'scene', 'surface', 'material', 'mat4', 'vec3')})
        picovector = types.SimpleNamespace(**{name: never_call for name in ('font', 'rect', 'color', 'image')})
        namespace = {"json": json, "builtins": types.SimpleNamespace(),
                     "sys": types.SimpleNamespace(modules={".frozen/badgeware/badge": module},
                                                  implementation=types.SimpleNamespace(name="micropython")),
                     "os": types.SimpleNamespace(uname=lambda: types.SimpleNamespace(machine="GitHub Badger 2350 with RP2350"),
                                                 statvfs=lambda path: (512, 512, 0, 2000)),
                     "vfs": types.SimpleNamespace(mount=lambda: [(object(), "/system")]),
                     "USER_FLASH_SIZE": 13631488, "LFS_SIZE": 1048576, "_rs_exists": lambda path: True}
        if active:
            badge = types.SimpleNamespace(**{name: never_call for name in
                ('mode', 'buttons', 'pad', 'pressed', 'ticks_delta', 'default_clear', 'imu',
                 'autorotate', 'rotate_180', 'imu_upright')})
            badge._internal_i2c = types.SimpleNamespace(readfrom_mem=never_call, writeto_mem=never_call)
            namespace["badge"] = badge
            for name in ('screen', 'BUTTON_LEFT', 'BUTTON_RIGHT', 'BUTTON_UP', 'BUTTON_DOWN',
                         'BUTTON_SELECT', 'BUTTON_BACK', 'BUTTON_MENU', 'BUTTON_HOME', 'HIRES', 'VSYNC'):
                namespace[name] = 1
        with patch.dict(sys.modules, {"badgeware": badgeware, "pico3d": pico3d, "picovector": picovector}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            exec(installer.REMOTE_PREFLIGHT, namespace)
        return json.loads(output.getvalue())

    def test_idle_launcher_definitions_pass_without_constructing_badge_or_sensor(self):
        state = self.state()
        self.assertEqual(state["runtime_context"], "launcher_definitions")
        self.assertEqual(state["missing"], [])

    def test_missing_lazy_launcher_control_is_explicit(self):
        state = self.state(missing_control=True)
        self.assertIn("MENU_BUTTON", state["missing"])

    def test_active_runtime_globals_are_supported_as_well(self):
        state = self.state(active=True)
        self.assertEqual(state["runtime_context"], "active_runtime")
        self.assertEqual(state["missing"], [])


class DeviceOperationTests(unittest.TestCase):
    """Execute generated device code against a confined host filesystem."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "system/apps/roadster_sandbox"
        self.app.mkdir(parents=True)
        self.other = self.root / "system/apps/other_app"
        self.other.mkdir()
        (self.other / "__init__.py").write_bytes(b"other app")
        (self.root / "system/main.py").write_bytes(b"unchanged boot")
        self.mount = [object()]
        self.saved_mount = self.mount[0]
        self.readonly = [True]
        self.fail_rename_once = None
        self.fail_writable_mount = False
        def local(path):
            path = Path(path)
            self.assertTrue(path.is_absolute())
            self.assertNotIn("..", path.parts)
            return self.root / str(path).lstrip("/")
        def checked_write_path(path):
            self.assertTrue(path.startswith(installer.APP) or path.startswith("/roadster-transfer-"))
            if path.startswith("/system"):
                self.assertFalse(self.readonly[0], "device mutation bypassed writable remount")
            return local(path)
        def open_file(path, mode="r"):
            resolved = checked_write_path(path) if any(letter in mode for letter in "wa+") else local(path)
            return resolved.open(mode)
        def rename(source, target):
            first, second = checked_write_path(source), checked_write_path(target)
            if target == self.fail_rename_once:
                self.fail_rename_once = None
                raise OSError("rename interrupted")
            first.rename(second)
        fake_os = types.SimpleNamespace(
            stat=lambda path: local(path).stat(), listdir=lambda path: os.listdir(local(path)),
            mkdir=lambda path: checked_write_path(path).mkdir(),
            remove=lambda path: checked_write_path(path).unlink(),
            rmdir=lambda path: checked_write_path(path).rmdir(), rename=rename)
        def mount(*args, **kwargs):
            if not args:
                return [(self.mount[0], "/system")]
            if self.fail_writable_mount and args[0] != self.saved_mount:
                raise OSError("mount failed")
            self.mount[0] = args[0]
            self.readonly[0] = kwargs.get("readonly", False)
        fake_vfs = types.SimpleNamespace(mount=mount, umount=lambda path: None, VfsFat=lambda block: object())
        fake_rp2 = types.SimpleNamespace(Flash=lambda **kwargs: kwargs)
        self.namespace = {"USER_FLASH_SIZE": 16 * 1024 * 1024, "LFS_SIZE": 1024 * 1024, "open": open_file}
        with patch.dict(sys.modules, {"os": fake_os, "vfs": fake_vfs, "rp2": fake_rp2}):
            exec(installer.REMOTE_HELPERS, self.namespace)
        def execute(code, timeout=30):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                exec(code, self.namespace)
            return output.getvalue()
        self.device = installer.BadgeDevice(types.SimpleNamespace(execute=execute), {})

    def tearDown(self):
        self.assertEqual((self.other / "__init__.py").read_bytes(), b"other app")
        self.assertEqual((self.root / "system/main.py").read_bytes(), b"unchanged boot")

    def test_write_streams_and_verifies_complete_binary_without_touching_other_apps(self):
        content = bytes(range(256)) * 29
        self.device.write("models/mesh.bin", content)
        self.assertEqual(self.device.read("models/mesh.bin"), content)
        self.assertEqual(self.device.inventory()["models/mesh.bin"]["sha256"], installer.digest(content))
        self.assertIs(self.mount[0], self.saved_mount)
        self.assertTrue(self.readonly[0])
        self.assertFalse(list(self.root.glob("roadster-transfer-*")))

    def test_interrupted_rename_restores_previous_file_and_readonly_mount(self):
        (self.app / "physics.py").write_bytes(b"original")
        self.fail_rename_once = installer.APP + "/physics.py"
        with self.assertRaisesRegex(OSError, "rename interrupted"):
            self.device.write("physics.py", b"replacement")
        self.assertEqual((self.app / "physics.py").read_bytes(), b"original")
        self.assertEqual(list(self.app.iterdir()), [self.app / "physics.py"])
        self.assertTrue(self.readonly[0])
        self.assertFalse(list(self.root.glob("roadster-transfer-*")))

    def test_writable_mount_failure_cleans_transfer_and_restores_mount(self):
        self.fail_writable_mount = True
        with self.assertRaisesRegex(OSError, "mount failed"):
            self.device.write("physics.py", b"replacement")
        self.assertEqual(list(self.app.iterdir()), [])
        self.assertTrue(self.readonly[0])
        self.assertIs(self.mount[0], self.saved_mount)
        self.assertFalse(list(self.root.glob("roadster-transfer-*")))

    def test_secret_names_rejected_before_hash_or_read(self):
        (self.app / "secrets.py").write_bytes(b"must not read")
        with patch.dict(self.namespace, {"_rs_hash": lambda path: self.fail("private file was hashed")}):
            with self.assertRaises(AssertionError):
                self.device.inventory()

    def test_delete_and_prune_are_confined_to_app(self):
        self.device.write("models/mesh.bin", b"mesh")
        self.device.remove("models/mesh.bin")
        self.device.prune()
        self.assertFalse(self.app.exists())
        self.assertTrue(self.readonly[0])


class FakeSerial:
    def __init__(self, output=b"hello\n", errors=b""):
        self.pending = bytearray()
        self.output, self.errors = output, errors
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(data)
        if data == b"\x01":
            self.pending.extend(b"raw REPL; CTRL-B to exit\r\n>")
        elif data == b"\x04":
            self.pending.extend(b"OK" + self.output + b"\x04" + self.errors + b"\x04>")

    def read(self, count):
        result = bytes(self.pending[:count])
        del self.pending[:count]
        return result

    def reset_input_buffer(self):
        self.pending.clear()

    def close(self):
        self.closed = True


class RawProtocolTests(unittest.TestCase):
    def client(self, serial=None):
        client = RawRepl.__new__(RawRepl)
        client.serial = serial or FakeSerial()
        client.ready = False
        client.port = "explicit-port"
        return client

    @patch("badge_transport.time.sleep")
    def test_stdout_and_errors_are_separate_and_next_command_reuses_raw_prompt(self, sleep):
        client = self.client()
        self.assertEqual(client.execute("print('hello')"), "hello\n")
        self.assertEqual(client.execute("print('hello')"), "hello\n")
        self.assertEqual(client.serial.writes.count(b"\x01"), 1)

    @patch("badge_transport.time.sleep")
    def test_remote_exception_is_not_reported_as_success(self, sleep):
        client = self.client(FakeSerial(errors=b"Traceback: write failed"))
        with self.assertRaisesRegex(TransportError, "write failed"):
            client.execute("bad write")
        self.assertFalse(client.ready)

    def test_timeout_is_bounded(self):
        client = self.client()
        with patch("badge_transport.time.monotonic", side_effect=[0, 0.5, 2]):
            with self.assertRaisesRegex(TransportError, "timeout"):
                client._until(b">", 1)

    @patch("badge_transport.time.sleep")
    def test_close_never_sends_interrupt_or_reset_after_launch(self, sleep):
        old, fresh = FakeSerial(), FakeSerial()
        client = self.client(old)
        client.serial_module = types.SimpleNamespace(Serial=lambda *args, **kwargs: fresh)
        client.launch(installer.APP)
        writes = list(fresh.writes)
        self.assertEqual(fresh.writes[-1], b"\x04")
        self.assertIn(b"import badgeware\nbadgeware.launch('/system/apps/roadster_sandbox')\nbadgeware.reset()\n", fresh.writes)
        client.close()
        self.assertEqual(fresh.writes, writes)
        self.assertTrue(fresh.closed)


if __name__ == "__main__":
    unittest.main()
