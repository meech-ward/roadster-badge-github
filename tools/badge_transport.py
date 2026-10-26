"""Small USB-serial client for the MicroPython raw REPL.

This module deliberately has no device discovery, filesystem policy, firmware
flashing, or automatic reset. The installer selects and validates the target.
Only importing/opening this class requires the optional pyserial dependency.
"""

from __future__ import annotations

import time


class TransportError(RuntimeError):
    """The serial protocol failed or the remote program raised an error."""


class RawRepl:
    def __init__(self, port: str):
        try:
            import serial
        except ImportError as error:
            raise TransportError("Install USB support with: python3 -m pip install -r requirements.txt") from error
        self.port = port
        self.serial_module = serial
        self.serial = serial.Serial(port, 115200, timeout=0.1, write_timeout=5)
        self.ready = False

    def close(self):
        # Closing the host handle must not interrupt an app left running.
        self.serial.close()

    def _until(self, suffix: bytes, timeout: float, limit: int = 4 * 1024 * 1024) -> bytes:
        deadline = time.monotonic() + timeout
        result = bytearray()
        while time.monotonic() < deadline:
            byte = self.serial.read(1)
            if byte:
                result.extend(byte)
                if result.endswith(suffix):
                    return bytes(result)
                if len(result) > limit:
                    raise TransportError("Badge response exceeded the transfer limit")
        raise TransportError("Badge did not respond before the serial timeout")

    def enter(self):
        """Interrupt the current app and establish the raw-REPL prompt."""
        self.ready = False
        self.serial.write(b"\r\x03\x03")
        time.sleep(0.25)
        self.serial.write(b"\x02")
        time.sleep(0.1)
        self.serial.reset_input_buffer()
        self.serial.write(b"\x01")
        response = self._until(b">", 5)
        if b"raw REPL" not in response:
            raise TransportError("Selected port did not expose a MicroPython raw REPL")
        self.ready = True

    def _send(self, code: str):
        if not self.ready:
            self.enter()
        encoded = code.encode("utf-8")
        # Short writes avoid overrunning the device's USB receive buffer.
        for offset in range(0, len(encoded), 256):
            self.serial.write(encoded[offset:offset + 256])
            time.sleep(0.003)
        self.serial.write(b"\x04")
        acknowledgement = self._until(b"OK", 5, limit=64)
        if acknowledgement != b"OK":
            raise TransportError("Unexpected raw-REPL acknowledgement")

    def execute(self, code: str, timeout: float = 30) -> str:
        try:
            self._send(code)
            output = self._until(b"\x04", timeout)[:-1]
            errors = self._until(b"\x04", timeout)[:-1]
            self._until(b">", 5, limit=64)
            if errors.strip():
                raise TransportError(errors.decode("utf-8", errors="replace").strip())
            return output.decode("utf-8")
        except BaseException:
            self.ready = False
            raise

    def reset_to_menu(self):
        """Soft-reset into the existing firmware launcher, without a boot edit."""
        if not self.ready:
            self.enter()
        self.serial.write(b"\x02")
        time.sleep(0.1)
        self.serial.write(b"\x04")
        self.ready = False

    def launch(self, app_path: str):
        """Reset module caches, then start the installed app without a boot edit."""
        self.reset_to_menu()
        self.serial.close()
        # Allow the ordinary firmware boot to establish its injected globals.
        time.sleep(10)
        try:
            self.serial = self.serial_module.Serial(self.port, 115200, timeout=0.1, write_timeout=5)
        except self.serial_module.SerialException as error:
            raise TransportError("Badge did not return on the selected port after reset; run list again") from error
        self._send("import badgeware\nbadgeware.launch(%r)\nbadgeware.reset()\n" % app_path)
        self.ready = False
        # The app owns the interpreter now. An acknowledgement is not proof of
        # a rendered frame; the CLI explicitly reports launch as requested.
