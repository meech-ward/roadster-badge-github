"""Fresh tilt survives slow rendering; missed gesture history never replays."""
import importlib.util
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "roadster_slow_motion", ROOT / "apps/roadster_sandbox/motion.py")
motion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(motion)
WIDE = {"accel_scale": 1. / 8192., "gyro_scale": .035}


def raw(accel=(0., 0., 1.), gyro=(0., 0., 0.)):
    return (tuple(round(value / WIDE["accel_scale"]) for value in accel) +
            tuple(round(value / WIDE["gyro_scale"]) for value in gyro))


def step(controller, accel=(0., 0., 1.), gyro=(0., 0., 0.), *,
         dt=20., armed=True, frames=1):
    for _ in range(frames):
        state = controller.update(raw(accel, gyro), dt, armed)
    return state


def centered(grip=(0., 0., 1.), **options):
    controller = motion.MotionController(vertical_intent_ms=200., **WIDE, **options)
    step(controller, grip, frames=51, armed=False)
    assert controller.calibrated
    return controller


def lean(controller, axis, angle):
    radians = math.radians(angle)
    basis = controller.basis_x if axis == 0 else controller.basis_y
    return tuple(controller.neutral[i] * math.cos(radians) +
                 basis[i] * math.sin(radians) for i in range(3))


class RoadsterMotionStallTests(unittest.TestCase):
    def test_held_tilt_works_at_167_250_and_500_ms_in_all_directions_and_grips(self):
        grips = ((0., 0., 1.),
                 (0., math.sin(math.radians(70.)), math.cos(math.radians(70.))))
        for dt in (167., 250., 500.):
            for grip in grips:
                for sign in (-1, 1):
                    for axis, positive, negative in ((0, "right", "left"), (1, "up", "down")):
                        with self.subTest(dt=dt, grip=grip, sign=sign, axis=axis):
                            controller = centered(grip, horizontal_sign=sign, vertical_sign=sign)
                            for angle in (-24., 0., 15., 28.):
                                state = step(controller, lean(controller, axis, angle), dt=dt)
                                self.assertTrue(state["armed"])
                                self.assertTrue(state["tilt_valid"])
                                self.assertFalse(state["flick_active"])
                                self.assertFalse(state["flick_ready"])
                                self.assertEqual(state["event_count"], 0)
                                expected = max(0., min(1., (abs(angle) - 4.) / 24.))
                                channel, opposite = ((positive, negative) if angle >= 0 else
                                                     (negative, positive))
                                self.assertAlmostEqual(state["channels"][channel], expected, delta=.0004)
                                self.assertEqual(state["channels"][opposite], 0.)
                                self.assertEqual(sum(state["channels"].values()), state["channels"][channel])

    def test_slow_flick_only_mode_keeps_tilt_measurement_but_never_fires(self):
        controller = centered()
        controller.set_mode("flick")
        for dt in (167., 250., 500., 5000.):
            state = step(controller, lean(controller, 0, 24.), dt=dt)
            self.assertTrue(state["tilt_valid"])
            self.assertGreater(state["tilt_x"], .8)
            self.assertEqual(sum(state["channels"].values()), 0.)
            self.assertFalse(state["flick_ready"])

    def test_moving_tilt_follows_current_acceleration_at_every_slow_cadence(self):
        # A current angular sample cannot reconstruct the missed trajectory.
        # Both signs, including an end-of-stroke reversal, must leave fresh
        # acceleration authoritative instead of disabling continuous control.
        grips = ((0., 0., 1.),
                 (0., math.sin(math.radians(70.)), math.cos(math.radians(70.))))
        for grip in grips:
            for axis in (0, 1):
                controller = centered(grip)
                for dt in (167., 250., 500., 5000.):
                    for angle in (-24., 18.):
                        accel = lean(controller, axis, angle)
                        for rate in (-500., -60., 60., 500.):
                            # Exercise each raw gyro component, not only the
                            # +Z fixture's convenient roll/pitch components.
                            for gyro_axis in range(3):
                                gyro = tuple(rate if i == gyro_axis else 0. for i in range(3))
                                with self.subTest(grip=grip, axis=axis, dt=dt,
                                                  angle=angle, rate=rate, gyro_axis=gyro_axis):
                                    state = step(controller, accel, gyro, dt=dt)
                                    self.assertTrue(state["tilt_valid"])
                                    self.assertTrue(state["armed"])
                                    self.assertAlmostEqual(state["angle_x" if axis == 0 else "angle_y"],
                                                           angle, delta=.01)
                                    self.assertGreater(sum(state["channels"].values()), .5)
                                    self.assertFalse(state["flick_pending"])
                                    self.assertFalse(state["flick_active"])
                                    self.assertFalse(state["flick_ready"])
                                    self.assertEqual(state["event_count"], 0)

    def test_blocked_input_filters_new_tilt_without_firing_or_replaying(self):
        controller = centered()
        for dt in (20., 167., 250., 500.):
            state = step(controller, lean(controller, 0, 28.), dt=dt, armed=False, frames=10)
            self.assertTrue(state["tilt_valid"])
            self.assertFalse(state["armed"])
            self.assertEqual(sum(state["channels"].values()), 0.)
            self.assertEqual(state["flick_x"], 0.)
            self.assertFalse(state["flick_pending"])
        state = step(controller, lean(controller, 0, 28.), dt=500.)
        self.assertTrue(state["armed"])
        self.assertGreater(state["channels"]["right"], .99)
        self.assertEqual(state["event_count"], 0)

    def test_gap_does_not_integrate_latest_gyro_across_missing_time(self):
        controller = centered()
        step(controller, frames=20)
        old_gravity = controller.gravity
        for dt in (167., 250., 500., 5000.):
            state = step(controller, gyro=(0., -500., 0.), dt=dt)
            self.assertEqual(controller.gravity, old_gravity)
            self.assertTrue(state["tilt_valid"])
            self.assertTrue(state["armed"])
            self.assertEqual(state["angle_x"], 0.)
            self.assertEqual(state["angle_y"], 0.)
            self.assertEqual(sum(state["channels"].values()), 0.)
            self.assertFalse(state["flick_active"])
            self.assertEqual(state["event_count"], 0)
        state = step(controller, lean(controller, 0, -15.), dt=500.)
        self.assertTrue(state["tilt_valid"])
        self.assertAlmostEqual(state["angle_x"], -15., delta=.005)
        self.assertGreater(state["channels"]["left"], .45)

    def test_slow_freefall_impact_clipping_and_moving_translation_do_not_reuse_old_tilt(self):
        controller = centered()
        state = step(controller, lean(controller, 0, 24.), dt=500.)
        self.assertGreater(state["channels"]["right"], .8)
        rejected = (((0., 0., 0.), (0., 0., 0.)),
                    ((0., 0., 1.5), (0., 0., 0.)),
                    ((1., 0., 1.), (0., 0., 0.)),
                    ((0., 0., .8), (0., 45.1, 0.)),
                    ((0., 0., 1.2), (0., 60., 0.)),
                    ((.7, 0., 1.), (0., -120., 0.)),
                    ((0., 0., 1.), (0., 0., 1146.88)))
        for accel, gyro in rejected:
            with self.subTest(accel=accel, gyro=gyro):
                state = step(controller, accel, gyro, dt=500.)
                self.assertFalse(state["tilt_valid"])
                self.assertFalse(state["armed"])
                self.assertEqual(sum(state["channels"].values()), 0.)
                self.assertFalse(state["flick_active"])
        state = step(controller, dt=500.)
        self.assertTrue(state["tilt_valid"])
        self.assertEqual(state["tilt_x"], 0.)
        self.assertEqual(sum(state["channels"].values()), 0.)

    def test_moving_magnitude_confidence_uses_calibrated_gravity(self):
        for gravity_g in (.8, 1., 1.2):
            controller = centered((0., 0., gravity_g))
            self.assertAlmostEqual(controller.gravity_g, gravity_g, delta=.0001)
            direction = lean(controller, 0, 24.)
            for dt in (167., 250., 500., 5000.):
                for fraction, accepted in ((.9, True), (1., True), (1.05, True),
                                           (.84, False), (1.16, False)):
                    accel = tuple(value*gravity_g*fraction for value in direction)
                    with self.subTest(gravity_g=gravity_g, dt=dt, fraction=fraction):
                        state = step(controller, accel, (0., -60., 0.), dt=dt)
                        self.assertEqual(state["tilt_valid"], accepted)
                        self.assertEqual(state["armed"], accepted)
                        if accepted:
                            self.assertAlmostEqual(state["angle_x"], 24., delta=.01)
                            self.assertGreater(state["channels"]["right"], .8)
                        else:
                            self.assertEqual(sum(state["channels"].values()), 0.)
                        self.assertFalse(state["flick_active"])

    def test_quiet_samples_keep_wider_magnitude_range_and_moving_threshold_is_bias_corrected(self):
        controller = motion.MotionController(**WIDE)
        bias = (12., 20., -15.)
        step(controller, gyro=bias, frames=51, armed=False)
        direction = lean(controller, 0, 24.)
        for magnitude in (.72, .8, 1.2, 1.28):
            accel = tuple(value*magnitude for value in direction)
            for raw_rate, accepted in ((64., True), (66., False)):
                # Bias-corrected Y rates are 44 and 46 dps. The raw gyro
                # exceeds 45 in both cases; only actual motion tightens trust.
                state = step(controller, accel, (12., raw_rate, -15.), dt=250.)
                self.assertEqual(state["tilt_valid"], accepted)
                self.assertEqual(state["armed"], accepted)
                if not accepted:
                    self.assertEqual(sum(state["channels"].values()), 0.)

    def test_pending_vertical_flick_dies_on_gap_and_does_not_replay_on_fast_return(self):
        for dt in (167., 250., 500., 5000.):
            controller = centered()
            controller.set_mode("flick")
            step(controller, frames=20)
            self.assertTrue(step(controller, gyro=(240., 0., 0.))["flick_pending"])
            state = step(controller, lean(controller, 1, 24.), gyro=(240., 0., 0.), dt=dt)
            self.assertTrue(state["tilt_valid"])
            self.assertTrue(state["armed"])
            self.assertGreater(state["tilt_y"], .8)
            self.assertFalse(state["flick_pending"])
            self.assertFalse(state["flick_active"])
            self.assertFalse(state["flick_ready"])
            self.assertEqual(state["event_count"], 0)
            state = step(controller, gyro=(-240., 0., 0.), frames=5)
            self.assertFalse(state["flick_pending"])
            self.assertEqual(state["event_count"], 0)
            self.assertEqual(sum(state["channels"].values()), 0.)

    def test_active_horizontal_flick_dies_on_gap_and_new_quiet_then_flick_works(self):
        for dt in (167., 250., 500., 5000.):
            controller = centered()
            controller.set_mode("flick")
            step(controller, frames=20)
            self.assertTrue(step(controller, gyro=(0., -190., 0.))["flick_active"])
            state = step(controller, lean(controller, 0, 24.), gyro=(0., -190., 0.), dt=dt)
            self.assertTrue(state["tilt_valid"])
            self.assertTrue(state["armed"])
            self.assertGreater(state["tilt_x"], .8)
            self.assertFalse(state["flick_active"])
            self.assertFalse(state["flick_ready"])
            self.assertEqual(state["event_count"], 1)
            self.assertEqual(sum(state["channels"].values()), 0.)
            state = step(controller, gyro=(0., 190., 0.))
            self.assertEqual(state["event_count"], 1)
            self.assertEqual(sum(state["channels"].values()), 0.)
            state = step(controller, frames=40)
            self.assertTrue(state["flick_ready"])
            state = step(controller, gyro=(0., 190., 0.))
            self.assertEqual(state["event_count"], 2)
            self.assertGreater(state["channels"]["left"], 0.)

    def test_slow_calibration_requires_ten_quiet_observations_and_never_arms(self):
        for dt in (167., 250., 500.):
            controller = motion.MotionController(**WIDE)
            state = step(controller, dt=dt, frames=9)
            self.assertFalse(state["calibrated"])
            self.assertAlmostEqual(state["calibration_progress"], .9)
            state = step(controller, dt=dt)
            self.assertTrue(state["calibrated"])
            self.assertFalse(state["tilt_valid"])
            self.assertFalse(state["armed"])
            self.assertEqual(sum(state["channels"].values()), 0.)
            state = step(controller, dt=dt)
            self.assertTrue(state["tilt_valid"])
            self.assertTrue(state["armed"])

    def test_long_gap_or_motion_restarts_partial_calibration(self):
        controller = motion.MotionController(**WIDE)
        self.assertAlmostEqual(step(controller, frames=20)["calibration_progress"], .4)
        self.assertEqual(step(controller, dt=5000.)["calibration_progress"], 0.)
        self.assertAlmostEqual(step(controller, dt=500., frames=5)["calibration_progress"], .5)
        self.assertEqual(step(controller, (.3, 0., 1.), dt=500.)["calibration_progress"], 0.)
        self.assertFalse(controller.calibrated)

    def test_moving_slow_samples_never_calibrate_or_arm(self):
        for dt in (167., 250., 500., 5000.):
            controller = motion.MotionController(**WIDE)
            for rate in (45.15, 60., 500.):
                state = step(controller, gyro=(0., rate, 0.), dt=dt, frames=12)
                self.assertEqual(state["calibration_progress"], 0.)
                self.assertFalse(state["calibrated"])
                self.assertFalse(state["tilt_valid"])
                self.assertFalse(state["armed"])
                self.assertEqual(sum(state["channels"].values()), 0.)

    def test_nonpositive_or_nonfinite_timing_never_produces_control(self):
        for dt in (0., -10., float("inf"), float("-inf"), float("nan")):
            controller = centered()
            step(controller, lean(controller, 0, 28.), dt=500.)
            state = step(controller, lean(controller, 0, 28.), dt=dt)
            self.assertFalse(state["tilt_valid"])
            self.assertFalse(state["armed"])
            self.assertEqual(sum(state["channels"].values()), 0.)

    def test_clipped_sample_never_corrupts_gravity_or_replays_hidden_flick(self):
        for dt in (20., 150., 167., 500., 5000.):
            for component in range(6):
                for rail in (-32768, 32767):
                    with self.subTest(dt=dt, component=component, rail=rail):
                        controller = centered()
                        step(controller, frames=20)
                        trusted_gravity = controller.gravity
                        sample = list(raw())
                        sample[component] = rail
                        clipped = controller.update(tuple(sample), dt, True)
                        self.assertTrue(clipped["sensor_clipped"])
                        self.assertFalse(clipped["tilt_valid"])
                        self.assertFalse(clipped["armed"])
                        self.assertEqual(controller.gravity, trusted_gravity)
                        self.assertEqual(sum(clipped["channels"].values()), 0.)
                        self.assertFalse(clipped["flick_active"])
                        self.assertFalse(clipped["flick_pending"])
                        fresh = step(controller)
                        self.assertFalse(fresh["sensor_clipped"])
                        self.assertTrue(fresh["tilt_valid"])
                        self.assertTrue(fresh["armed"])
                        self.assertEqual(fresh["angle_x"], 0.)
                        self.assertEqual(fresh["angle_y"], 0.)
                        self.assertEqual(sum(fresh["channels"].values()), 0.)
                        self.assertEqual(fresh["event_count"], 0)
                        self.assertFalse(fresh["flick_active"])
                        self.assertFalse(fresh["flick_pending"])

    def test_clipping_discards_existing_burst_and_unfinished_calibration(self):
        controller = centered()
        step(controller, frames=20)
        self.assertTrue(step(controller, gyro=(0., -190., 0.))["flick_active"])
        clipped = controller.update((0, 0, 8192, 0, -32768, 0), 20., True)
        self.assertFalse(clipped["flick_active"])
        fresh = step(controller)
        self.assertFalse(fresh["flick_active"])
        self.assertEqual(fresh["event_count"], 1)
        controller = motion.MotionController(**WIDE)
        self.assertAlmostEqual(step(controller, frames=20)["calibration_progress"], .4)
        clipped = controller.update((0, 0, 8192, 0, -32768, 0), 20., True)
        self.assertEqual(clipped["calibration_progress"], 0.)
        self.assertFalse(clipped["calibrated"])
        self.assertFalse(clipped["tilt_valid"])

    def test_slow_bias_correction_uses_calibrated_bias_for_quiet_gate(self):
        controller = motion.MotionController(**WIDE)
        step(controller, gyro=(12., 20., -15.), frames=51, armed=False)
        self.assertTrue(controller.calibrated)
        state = step(controller, lean(controller, 0, 24.), gyro=(12., 20., -15.), dt=500.)
        self.assertTrue(state["tilt_valid"])
        self.assertGreater(state["channels"]["right"], .8)
        self.assertLess(abs(state["rate_x"]), .001)
        self.assertLess(abs(state["rate_y"]), .001)


if __name__ == "__main__":
    unittest.main()
