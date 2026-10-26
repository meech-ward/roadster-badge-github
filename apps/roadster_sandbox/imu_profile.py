"""Temporary LSM6DS3TR-C ranges; preserve and restore the firmware's settings.

The bus must implement real readfrom_mem/writeto_mem operations. No hardware is
accessed at import or construction, and unsupported buses fail explicitly.
"""

IMU_ADDRESS = 0x6A
CTRL1_XL = 0x10
CTRL2_G = 0x11
REGISTERS = (CTRL1_XL, CTRL2_G)


class MotionImuProfile:
    def __init__(self, bus):
        self.bus = bus
        self.active = False
        self.accel_scale = None
        self.gyro_scale = None
        self.original_regs = None
        self.requested_regs = None
        self.current_regs = None
        self.last_error = ''
        self._needs_restore = False

    def _read(self, register):
        data = self.bus.readfrom_mem(IMU_ADDRESS, register, 1)
        if len(data) != 1:
            raise RuntimeError('IMU register 0x%02x returned %d bytes' %
                               (register, len(data)))
        return data[0]

    def _write(self, register, value):
        self.bus.writeto_mem(IMU_ADDRESS, register, bytes((value,)))

    def _set_current(self, values):
        self.current_regs = values
        accel, gyro = values
        self.accel_scale = (None if accel is None else
                            (1. / 16384., 1. / 2048., 1. / 8192.,
                             1. / 4096.)[(accel >> 2) & 3])
        self.gyro_scale = (None if gyro is None else
                           .004375 if gyro & 2 else
                           (.00875, .0175, .035, .07)[(gyro >> 2) & 3])

    def enable(self):
        """Select +/-4 g and +/-1000 dps, or roll back and raise on failure."""
        if self.active:
            return True
        if self._needs_restore:
            raise RuntimeError('IMU settings require restoration before enabling')

        try:
            original = tuple(self._read(register) for register in REGISTERS)
        except Exception as error:
            self.last_error = 'IMU profile read failed: ' + str(error)
            raise RuntimeError(self.last_error)
        self.original_regs = original
        self._set_current(original)
        # FS_XL=10: +/-4 g; FS_G=10: +/-1000 dps. Clear FS_125 as well.
        # ODR and every other control bit retain the firmware's exact values.
        self.requested_regs = ((original[0] & ~0x0C) | 0x08,
                               (original[1] & ~0x0E) | 0x08)
        self._needs_restore = True
        try:
            for register, value in zip(REGISTERS, self.requested_regs):
                self._write(register, value)
            actual = tuple(self._read(register) for register in REGISTERS)
            self._set_current(actual)
            if actual != self.requested_regs:
                raise RuntimeError('IMU range readback mismatch: %s != %s' %
                                   (actual, self.requested_regs))
        except Exception as error:
            message = 'IMU profile enable failed: ' + str(error)
            try:
                self.restore()
            except Exception as restore_error:
                message += '; ' + str(restore_error)
            self.last_error = message
            raise RuntimeError(message)
        self.active = True
        self.last_error = ''
        return True

    def restore(self):
        """Restore both registers and verify both, even if one operation fails."""
        if not self._needs_restore:
            return True
        self.active = False
        errors = []
        for register, value in zip(REGISTERS, self.original_regs):
            try:
                self._write(register, value)
            except Exception as error:
                errors.append('write 0x%02x: %s' % (register, error))
        actual = []
        for register, expected in zip(REGISTERS, self.original_regs):
            try:
                value = self._read(register)
                actual.append(value)
                if value != expected:
                    errors.append('readback 0x%02x: %d != %d' %
                                  (register, value, expected))
            except Exception as error:
                actual.append(None)
                errors.append('read 0x%02x: %s' % (register, error))
        self._set_current(tuple(actual))
        if errors:
            self.last_error = 'IMU restoration failed: ' + '; '.join(errors)
            raise RuntimeError(self.last_error)
        self._needs_restore = False
        self.last_error = ''
        return True

    def snapshot(self):
        return {
            'active': self.active, 'needs_restore': self._needs_restore,
            'original_regs': self.original_regs,
            'requested_regs': self.requested_regs,
            'current_regs': self.current_regs,
            'accel_scale': self.accel_scale, 'gyro_scale': self.gyro_scale,
            'last_error': self.last_error,
        }
