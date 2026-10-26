"""Register transaction checks; no physical badge or simulated sensor claims."""
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox/imu_profile.py'
SPEC = importlib.util.spec_from_file_location('motion_imu_profile', SOURCE)
imu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(imu)


class RegisterBus:
    def __init__(self, accel=0x40, gyro=0x40):
        self.registers = {0x10: accel, 0x11: gyro}
        self.reads = []
        self.writes = []
        self.fail_reads = set()
        self.fail_writes = set()
        self.read_values = {}
        self.short_reads = set()

    def readfrom_mem(self, address, register, size):
        assert address == 0x6A and size == 1
        self.reads.append(register)
        count = len(self.reads)
        if count in self.fail_reads:
            raise OSError('read failed')
        if count in self.short_reads:
            return b''
        return bytes((self.read_values.get(count, self.registers[register]),))

    def writeto_mem(self, address, register, data):
        assert address == 0x6A and len(data) == 1
        self.writes.append((register, data[0]))
        if len(self.writes) in self.fail_writes:
            raise OSError('write failed')
        self.registers[register] = data[0]


class MotionImuProfileTests(unittest.TestCase):
    def test_construction_and_unused_restore_do_not_access_bus(self):
        bus = RegisterBus()
        profile = imu.MotionImuProfile(bus)
        self.assertFalse(profile.active)
        self.assertIsNone(profile.accel_scale)
        self.assertIsNone(profile.gyro_scale)
        self.assertTrue(profile.restore())
        self.assertEqual(bus.reads, [])
        self.assertEqual(bus.writes, [])

    def test_stock_profile_round_trip_and_idempotence(self):
        bus = RegisterBus()
        profile = imu.MotionImuProfile(bus)
        self.assertTrue(profile.enable())
        self.assertEqual(bus.registers, {0x10: 0x48, 0x11: 0x48})
        self.assertEqual(profile.accel_scale, 1 / 8192)
        self.assertEqual(profile.gyro_scale, .035)
        state = profile.snapshot()
        self.assertTrue(state['active'])
        self.assertEqual(state['original_regs'], (0x40, 0x40))
        self.assertEqual(state['current_regs'], (0x48, 0x48))
        transactions = (len(bus.reads), len(bus.writes))
        self.assertTrue(profile.enable())
        self.assertEqual((len(bus.reads), len(bus.writes)), transactions)
        self.assertTrue(profile.restore())
        self.assertEqual(bus.registers, {0x10: 0x40, 0x11: 0x40})
        self.assertFalse(profile.active)
        self.assertEqual(profile.accel_scale, 1 / 16384)
        self.assertEqual(profile.gyro_scale, .00875)
        transactions = (len(bus.reads), len(bus.writes))
        self.assertTrue(profile.restore())
        self.assertEqual((len(bus.reads), len(bus.writes)), transactions)

    def test_preserves_every_unrelated_bit_and_restores_125_dps(self):
        for accel, gyro in ((0xB7, 0xD7), (0xFF, 0xFF), (0, 0)):
            bus = RegisterBus(accel, gyro)
            profile = imu.MotionImuProfile(bus)
            profile.enable()
            self.assertEqual(bus.registers[0x10] & ~0x0C, accel & ~0x0C)
            self.assertEqual(bus.registers[0x11] & ~0x0E, gyro & ~0x0E)
            self.assertEqual(bus.registers[0x10] & 0x0C, 0x08)
            self.assertEqual(bus.registers[0x11] & 0x0E, 0x08)
            profile.restore()
            self.assertEqual(bus.registers, {0x10: accel, 0x11: gyro})
            if gyro & 2:
                self.assertEqual(profile.gyro_scale, .004375)

    def test_initial_read_failure_causes_no_write(self):
        bus = RegisterBus()
        bus.fail_reads.add(2)
        profile = imu.MotionImuProfile(bus)
        with self.assertRaisesRegex(RuntimeError, 'profile read failed'):
            profile.enable()
        self.assertEqual(bus.writes, [])
        self.assertFalse(profile.active)

    def test_second_write_failure_rolls_back_both_saved_registers(self):
        bus = RegisterBus()
        bus.fail_writes.add(2)
        profile = imu.MotionImuProfile(bus)
        with self.assertRaisesRegex(RuntimeError, 'enable failed.*write failed'):
            profile.enable()
        self.assertEqual(bus.registers, {0x10: 0x40, 0x11: 0x40})
        self.assertEqual(bus.writes[-2:], [(0x10, 0x40), (0x11, 0x40)])
        self.assertFalse(profile.snapshot()['needs_restore'])
        self.assertFalse(profile.active)

    def test_mismatched_or_failed_readback_rolls_back(self):
        for failure in ('mismatch', 'read', 'short'):
            bus = RegisterBus()
            if failure == 'mismatch':
                bus.read_values[3] = 0x40
            elif failure == 'read':
                bus.fail_reads.add(3)
            else:
                bus.short_reads.add(3)
            profile = imu.MotionImuProfile(bus)
            with self.assertRaisesRegex(RuntimeError, 'enable failed'):
                profile.enable()
            self.assertEqual(bus.registers, {0x10: 0x40, 0x11: 0x40})
            self.assertFalse(profile.active)
            self.assertFalse(profile.snapshot()['needs_restore'])

    def test_restore_write_failure_still_attempts_both_and_can_retry(self):
        bus = RegisterBus()
        profile = imu.MotionImuProfile(bus)
        profile.enable()
        bus.fail_writes.add(3)
        with self.assertRaisesRegex(RuntimeError, 'restoration failed.*0x10'):
            profile.restore()
        self.assertEqual(bus.writes[-2:], [(0x10, 0x40), (0x11, 0x40)])
        self.assertEqual(bus.reads[-2:], [0x10, 0x11])
        self.assertEqual(bus.registers[0x11], 0x40)
        self.assertTrue(profile.snapshot()['needs_restore'])
        with self.assertRaisesRegex(RuntimeError, 'require restoration'):
            profile.enable()
        profile.restore()
        self.assertEqual(bus.registers, {0x10: 0x40, 0x11: 0x40})

    def test_restore_read_failure_is_explicit_and_checks_other_register(self):
        bus = RegisterBus()
        profile = imu.MotionImuProfile(bus)
        profile.enable()
        bus.fail_reads.add(5)
        with self.assertRaisesRegex(RuntimeError, 'restoration failed.*read 0x10'):
            profile.restore()
        self.assertEqual(bus.reads[-2:], [0x10, 0x11])
        self.assertEqual(profile.snapshot()['current_regs'], (None, 0x40))
        self.assertIsNone(profile.accel_scale)
        self.assertEqual(profile.gyro_scale, .00875)
        profile.restore()
        self.assertFalse(profile.snapshot()['needs_restore'])

    def test_setup_and_rollback_errors_both_remain_visible(self):
        bus = RegisterBus()
        bus.fail_writes.update((2, 3))
        profile = imu.MotionImuProfile(bus)
        with self.assertRaisesRegex(RuntimeError, 'enable failed.*restoration failed'):
            profile.enable()
        self.assertTrue(profile.snapshot()['needs_restore'])
        self.assertFalse(profile.active)
        profile.restore()
        self.assertEqual(bus.registers, {0x10: 0x40, 0x11: 0x40})

    def test_reenable_captures_the_latest_firmware_settings(self):
        bus = RegisterBus()
        profile = imu.MotionImuProfile(bus)
        profile.enable()
        profile.restore()
        bus.registers = {0x10: 0x60, 0x11: 0x62}
        profile.enable()
        self.assertEqual(profile.snapshot()['original_regs'], (0x60, 0x62))
        profile.restore()
        self.assertEqual(bus.registers, {0x10: 0x60, 0x11: 0x62})


if __name__ == '__main__':
    unittest.main()
