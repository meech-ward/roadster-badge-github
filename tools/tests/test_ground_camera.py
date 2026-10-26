"""Ground-camera response and invalid-sample behavior, independent of firmware."""
import importlib.util
from pathlib import Path
import unittest

APP = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox'
SPEC = importlib.util.spec_from_file_location('roadster_ground_camera', APP / 'ground_camera.py')
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)


def sample(x=0., y=0., **values):
    return dict(calibrated=True, tilt_valid=True, sensor_clipped=False,
                tilt_x=x, tilt_y=y, **values)


class GroundCameraTests(unittest.TestCase):
    def test_four_axes_move_independently_without_armed_jet_channels(self):
        for x, y in ((1., 0.), (-1., 0.), (0., 1.), (0., -1.)):
            camera = c.GroundCamera()
            sensed = sample(x, y, armed=False, mode='flick', channels={})
            before = sensed.copy()
            camera.update(.025, sensed, True)
            self.assertTrue(camera.active)
            self.assertGreater(camera.yaw*x - camera.height*y, 0.)
            self.assertEqual(camera.yaw == 0., x == 0.)
            self.assertEqual(camera.height == 0., y == 0.)
            self.assertEqual(sensed, before)

    def test_range_is_bounded_and_never_overshoots(self):
        camera = c.GroundCamera()
        for _ in range(100):
            camera.update(.025, sample(9., -9.), True)
            self.assertLessEqual(camera.yaw, c.MAX_YAW)
            self.assertLessEqual(camera.height, c.MAX_HEIGHT)
        self.assertAlmostEqual(camera.yaw, c.MAX_YAW, places=5)
        self.assertAlmostEqual(camera.height, c.MAX_HEIGHT, places=4)
        before = camera.yaw
        camera.update(.025, sample(-1., 1.), True)
        self.assertLess(camera.yaw, before)
        self.assertGreater(camera.yaw, -c.MAX_YAW)

    def test_center_returns_smoothly_and_disabled_camera_ignores_air_lean(self):
        for enabled in (True, False):
            camera = c.GroundCamera()
            for _ in range(30):
                camera.update(.025, sample(1., -1.), True)
            before = camera.yaw, camera.height
            target = sample() if enabled else sample(-1., 1.)
            camera.update(.025, target, enabled)
            self.assertGreater(camera.yaw, 0.)
            self.assertLess(camera.yaw, before[0])
            self.assertGreater(camera.height, 0.)
            self.assertLess(camera.height, before[1])
            for _ in range(100):
                camera.update(.025, target, enabled)
            self.assertEqual((camera.yaw, camera.height), (0., 0.))
            self.assertEqual(camera.active, enabled)

    def test_invalid_or_unavailable_sample_cannot_drive_camera(self):
        for change in ({'calibrated': False}, {'tilt_valid': False},
                       {'sensor_clipped': True}, None):
            camera = c.GroundCamera()
            camera.update(.025, sample(1., 1.), True)
            before = camera.yaw, camera.height
            sensed = sample(-1., -1.) if change is not None else None
            if change:
                sensed.update(change)
            camera.update(.025, sensed, True)
            self.assertFalse(camera.active)
            self.assertGreater(camera.yaw, 0.)
            self.assertLess(camera.yaw, before[0])
            self.assertLess(camera.height, 0.)
            self.assertGreater(camera.height, before[1])

    def test_slow_or_invalid_timing_cannot_snap_or_corrupt_offsets(self):
        camera, reference = c.GroundCamera(), c.GroundCamera()
        camera.update(3., sample(1., 1.), True)
        reference.update(.1, sample(1., 1.), True)
        self.assertEqual((camera.yaw, camera.height), (reference.yaw, reference.height))
        for dt in (0., -1., float('inf'), float('nan')):
            camera.update(dt, sample(-1., -1.), True)
            self.assertEqual((camera.yaw, camera.height), (reference.yaw, reference.height))

    def test_reset_discards_view_and_active_state(self):
        camera = c.GroundCamera()
        camera.update(.025, sample(1., 1.), True)
        camera.reset()
        self.assertFalse(camera.active)
        self.assertEqual((camera.yaw, camera.height), (0., 0.))


if __name__ == '__main__':
    unittest.main()
