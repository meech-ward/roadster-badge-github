"""Motion-jet requests from physical-style IMU traces, independent of UI."""
import importlib.util
import math
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "apps/roadster_sandbox/motion.py"
SPEC = importlib.util.spec_from_file_location("roadster_motion_controller", SOURCE)
motion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(motion)


WIDE_RANGE = {"accel_scale": 1. / 8192., "gyro_scale": .035}


def raw(accel=(0., 0., 1.), gyro=(0., 0., 0.), *,
        accel_scale=motion.ACCEL_SCALE, gyro_scale=motion.GYRO_SCALE):
    sample = (tuple(round(v / accel_scale) for v in accel) +
              tuple(round(v / gyro_scale) for v in gyro))
    if not all(-32768 <= value <= 32767 for value in sample):
        raise ValueError("Physical test trace exceeds the configured sensor range")
    return sample


def raw_for(controller, accel=(0., 0., 1.), gyro=(0., 0., 0.)):
    return raw(accel, gyro, accel_scale=controller.accel_scale,
               gyro_scale=controller.gyro_scale)


def step(controller, sample=None, frames=1, armed=False, dt=20):
    for _ in range(frames):
        state = controller.update(sample or raw_for(controller), dt, armed)
    return state


def centered(accel=(0., 0., 1.), gyro=(0., 0., 0.), **kwargs):
    controller = motion.MotionController(**kwargs)
    step(controller, raw_for(controller, accel, gyro), frames=51)
    assert controller.calibrated
    return controller


def vertical_trace(controller, speeds, angle=70.):
    """A consistent accel/gyro trace from a steep, face-facing playing grip."""
    states = []
    for speed in speeds:
        angle += speed * .02
        gravity = (0., math.sin(math.radians(angle)), math.cos(math.radians(angle)))
        states.append(step(controller, raw_for(controller, gravity, (speed, 0., 0.)),
                           armed=True))
    return states, angle


class MotionControllerTests(unittest.TestCase):
    def test_calibration_requires_continuous_quiet_second(self):
        controller = motion.MotionController()
        state = step(controller, frames=30, armed=True)
        self.assertFalse(state["calibrated"])
        self.assertEqual(sum(state["channels"].values()), 0.)
        state = step(controller, raw((.2, 0., .98)), armed=True)
        self.assertEqual(state["calibration_progress"], 0.)
        state = step(controller, frames=49)
        self.assertFalse(state["calibrated"])
        self.assertTrue(step(controller)["calibrated"])

    def test_static_gyro_bias_is_calibrated_not_mistaken_for_motion(self):
        bias = (2.1, -3.5, -2.6)
        controller = centered(accel=(.04, -.02, -.96), gyro=bias)
        for measured, expected in zip(controller.gyro_bias, bias):
            self.assertAlmostEqual(measured, expected, delta=.005)
        state = step(controller, raw((.04, -.02, -.96), bias), 60, armed=True)
        self.assertAlmostEqual(state["tilt_x"], 0.)
        self.assertAlmostEqual(state["tilt_y"], 0.)
        self.assertEqual(state["event_count"], 0)
        self.assertEqual(sum(state["channels"].values()), 0.)

    def test_calibration_rejects_rotation_and_freefall(self):
        for sample in (raw(gyro=(60., 0., 0.)), raw((0., 0., 0.))):
            controller = motion.MotionController()
            state = step(controller, sample, 100)
            self.assertFalse(state["calibrated"])
            self.assertEqual(state["calibration_progress"], 0.)

    def test_tilt_four_directions_are_proportional_and_opposite_exclusive(self):
        for axis, channel, opposite in ((0, "right", "left"), (1, "up", "down")):
            for sign in (-1, 1):
                values = []
                for degrees in (3., 15., 30.):
                    controller = centered()
                    controller.set_mode("tilt")
                    a = [0., 0., math.cos(math.radians(degrees))]
                    a[axis] = math.sin(math.radians(degrees)) * sign
                    state = step(controller, raw(a), 150, armed=True)
                    target, other = (channel, opposite) if sign > 0 else (opposite, channel)
                    values.append(state["channels"][target])
                    self.assertEqual(state["channels"][other], 0.)
                self.assertEqual(values[0], 0.)
                self.assertGreater(values[1], .4)
                self.assertLess(values[1], .6)
                self.assertEqual(values[2], 1.)

    def test_upright_grip_preserves_forward_back_sensitivity(self):
        for gravity in ((0., 1., 0.), (0., -1., 0.)):
            controller = centered(accel=gravity)
            controller.set_mode("tilt")
            self.assertAlmostEqual(motion._dot(controller.basis_x, controller.neutral), 0.)
            self.assertAlmostEqual(motion._dot(controller.basis_y, controller.neutral), 0.)
            angle = math.radians(25.)
            moved = tuple(gravity[i] * math.cos(angle) + controller.basis_y[i] * math.sin(angle)
                          for i in range(3))
            state = step(controller, raw(moved), 120, armed=True)
            self.assertGreater(state["channels"]["up"], .8)
            self.assertLess(abs(state["tilt_x"]), .001)

    def test_screen_rotation_flips_both_request_directions(self):
        normal = centered()
        inverted = centered(horizontal_sign=-1, vertical_sign=-1)
        for controller in (normal, inverted):
            controller.set_mode("tilt")
            step(controller, raw((.3, .3, math.sqrt(.82))), 150, armed=True)
        self.assertAlmostEqual(normal.tilt_x, -inverted.tilt_x)
        self.assertAlmostEqual(normal.tilt_y, -inverted.tilt_y)
        self.assertGreater(normal.channels["right"], .5)
        self.assertGreater(inverted.channels["left"], .5)

    def test_faster_flick_requests_stronger_bounded_pulse(self):
        levels = []
        for dps in (50., 210., 350., 550.):
            controller = centered(**WIDE_RANGE)
            controller.set_mode("flick")
            step(controller, frames=20, armed=True)
            state = step(controller, raw_for(controller, gyro=(0., -dps, 0.)), armed=True)
            levels.append(state["channels"]["right"])
            self.assertEqual(state["channels"]["left"], 0.)
            self.assertLessEqual(state["channels"]["right"], 1.)
        self.assertEqual(levels[0], 0.)
        self.assertGreater(levels[1], .1)
        self.assertGreater(levels[2], levels[1])
        self.assertEqual(levels[3], 1.)

    def test_axis_angular_curves_are_symmetric_monotonic_and_reach_full(self):
        levels = {}
        for axis, positive, negative in ((0, "right", "left"), (1, "up", "down")):
            for sign, target in ((1, positive), (-1, negative)):
                amounts = []
                for dps in (30., 70., 120., 200., 350., 550.):
                    controller = centered(**WIDE_RANGE)
                    controller.set_mode("flick")
                    step(controller, frames=20, armed=True)
                    gyro = (0., -dps * sign, 0.) if axis == 0 else (dps * sign, 0., 0.)
                    state = step(controller, raw_for(controller, gyro=gyro), armed=True)
                    expected = max(0., min(1., (dps - motion.FLICK_DEAD_DPS[axis]) /
                                          (motion.FLICK_FULL_DPS[axis] -
                                           motion.FLICK_DEAD_DPS[axis])))
                    # Half of one wide-range gyro count can shift a narrow
                    # response curve; retain a bound derived from that input.
                    self.assertAlmostEqual(state["channels"][target], expected,
                                           delta=.018 / (motion.FLICK_FULL_DPS[axis] -
                                                         motion.FLICK_DEAD_DPS[axis]))
                    self.assertFalse(state["sensor_clipped"])
                    for other, value in state["channels"].items():
                        if other != target:
                            self.assertEqual(value, 0.)
                    amounts.append(state["channels"][target])
                self.assertEqual(amounts, sorted(amounts))
                self.assertEqual(amounts[-1], 1.)
                levels[(axis, sign)] = amounts
            self.assertEqual(levels[(axis, 1)], levels[(axis, -1)])
        # Moderate pitching stays below onset; stronger deliberate strokes
        # retain a useful range rather than instantly requesting full output.
        self.assertEqual(levels[(1, 1)][2], 0.)
        self.assertLess(levels[(1, 1)][3], levels[(0, 1)][3])
        self.assertLess(levels[(1, 1)][3], .2)
        self.assertGreater(levels[(1, 1)][4], .9)

    def test_axis_linear_curves_are_symmetric_monotonic_and_reach_full(self):
        levels = {}
        for axis, positive, negative in ((0, "right", "left"), (1, "up", "down")):
            for sign, target in ((1, positive), (-1, negative)):
                amounts = []
                for acceleration in (.1, .3, .6, .9, 1.3, 1.7):
                    controller = centered(**WIDE_RANGE)
                    controller.set_mode("flick")
                    step(controller, frames=20, armed=True)
                    accel = [0., 0., 1.]
                    accel[axis] = acceleration * sign
                    state = step(controller, raw_for(controller, accel=accel), armed=True)
                    expected = max(0., min(1., (acceleration - motion.LINEAR_DEAD_G[axis]) /
                                          (motion.LINEAR_FULL_G[axis] -
                                           motion.LINEAR_DEAD_G[axis])))
                    self.assertAlmostEqual(state["channels"][target], expected, delta=.0002)
                    self.assertFalse(state["sensor_clipped"])
                    for other, value in state["channels"].items():
                        if other != target:
                            self.assertEqual(value, 0.)
                    amounts.append(state["channels"][target])
                self.assertEqual(amounts, sorted(amounts))
                self.assertEqual(amounts[-1], 1.)
                levels[(axis, sign)] = amounts
            self.assertEqual(levels[(axis, 1)], levels[(axis, -1)])
        self.assertEqual(levels[(1, 1)][2], 0.)
        self.assertLess(levels[(1, 1)][3], levels[(0, 1)][3])
        self.assertLess(levels[(1, 1)][3], .2)
        self.assertGreater(levels[(1, 1)][4], .7)

    def test_conversion_scales_preserve_same_physical_motion(self):
        for axis in (0, 1):
            stock, wide = centered(), centered(**WIDE_RANGE)
            for controller in (stock, wide):
                step(controller, frames=20, armed=True)
            angle = 0.
            trace = []
            for speed in (0., 20., 40., 70., 130., 190., 0., 0.):
                angle += math.radians(speed * .02)
                accel = [0., 0., math.cos(angle)]
                accel[axis] = math.sin(angle)
                gyro = (0., -speed, 0.) if axis == 0 else (speed, 0., 0.)
                trace.append((accel, gyro))
            trace += [(trace[-1][0], (0., 0., 0.))] * 30
            for accel, gyro in trace:
                states = [step(c, raw_for(c, accel, gyro), armed=True) for c in (stock, wide)]
                for key in ("angle_x", "angle_y", "rate_x", "rate_y", "linear_x", "linear_y",
                            "tilt_x", "tilt_y", "flick_x", "flick_y"):
                    self.assertAlmostEqual(states[0][key], states[1][key], delta=.025)
                self.assertEqual(states[0]["event_count"], states[1]["event_count"])
                for key in states[0]["channels"]:
                    self.assertAlmostEqual(states[0]["channels"][key], states[1]["channels"][key],
                                           delta=.002)
            for sign in (-1, 1):
                stock, wide = centered(), centered(**WIDE_RANGE)
                accel = [0., 0., 1.]
                accel[axis] = .6 * sign
                states = []
                for controller in (stock, wide):
                    controller.set_mode("flick")
                    step(controller, frames=20, armed=True)
                    states.append(step(controller, raw_for(controller, accel), armed=True))
                for key in states[0]["channels"]:
                    self.assertAlmostEqual(states[0]["channels"][key], states[1]["channels"][key],
                                           delta=.0002)
        self.assertEqual(stock.accel_scale, 1. / 16384.)
        self.assertEqual(stock.gyro_scale, .00875)

    def test_wide_range_slow_tilt_stays_below_flick_in_all_directions(self):
        for axis in (0, 1):
            for sign in (-1, 1):
                controller = centered(**WIDE_RANGE)
                controller.set_mode("flick")
                step(controller, frames=20, armed=True)
                for index in range(1, 51):
                    angle = math.radians(.6 * index * sign)
                    accel = [0., 0., math.cos(angle)]
                    accel[axis] = math.sin(angle)
                    gyro = (0., -30. * sign, 0.) if axis == 0 else (30. * sign, 0., 0.)
                    state = step(controller, raw_for(controller, accel, gyro), armed=True)
                    self.assertEqual(state["event_count"], 0)
                    self.assertEqual(sum(state["channels"].values()), 0.)
                self.assertGreater(abs(state["tilt_x" if axis == 0 else "tilt_y"]), .99)

    def test_raw_rail_is_reported_without_inventing_extra_motion(self):
        controller = centered(**WIDE_RANGE)
        for axis in range(6):
            for sign in (-1, 1):
                for count, clipped in ((32759, False), (32760, True), (32767, True)):
                    sample = list(raw_for(controller))
                    sample[axis] = sign * count
                    state = step(controller, tuple(sample))
                    self.assertEqual(state["sensor_clipped"], clipped)
                    self.assertEqual(sum(state["channels"].values()), 0.)
        self.assertFalse(step(controller)["sensor_clipped"])

    def test_trace_helper_rejects_unmeasurable_physical_values(self):
        with self.assertRaises(ValueError):
            raw(gyro=(500., 0., 0.))
        self.assertTrue(all(-32768 <= value <= 32767
                            for value in raw(gyro=(500., 0., 0.), **WIDE_RANGE)))
        with self.assertRaises(ValueError):
            raw(accel=(4.1, 0., 1.), **WIDE_RANGE)

    def test_natural_angular_acceleration_ramp_triggers_all_four_directions(self):
        # A real wrist movement accelerates through the quiet threshold before
        # reaching the flick threshold, instead of jumping there in one frame.
        for axis, sign, target in ((0, 1, "right"), (0, -1, "left"),
                                   (1, 1, "up"), (1, -1, "down")):
            controller = centered()
            controller.set_mode("flick")
            self.assertTrue(step(controller, frames=20, armed=True)["flick_ready"])
            angle = 0.
            for speed in (0., 20., 40., 70., 130., 190.):
                rate = speed * sign
                angle += math.radians(rate * .025)
                accel = [0., 0., math.cos(angle)]
                accel[axis] = math.sin(angle)
                gyro = (0., -rate, 0.) if axis == 0 else (rate, 0., 0.)
                state = step(controller, raw(accel, gyro), armed=True, dt=25)
            self.assertEqual(state["event_count"], 1)
            # Sideways motion now intentionally needs more speed. Check its
            # actual response curve rather than retaining the old 50% floor.
            measured_rate = state["rate_x" if axis == 0 else "rate_y"]
            expected = abs(motion._scaled(measured_rate, motion.FLICK_DEAD_DPS[axis],
                                          motion.FLICK_FULL_DPS[axis]))
            self.assertAlmostEqual(abs(state["last_flick"]["x" if axis == 0 else "y"]),
                                   expected)
            self.assertGreater(state["channels"][target], 0.)
            self.assertTrue(state["flick_active"])
            self.assertFalse(state["flick_ready"])
            self.assertGreater(state["flick_cooldown_ms"], 0.)
            for other in state["channels"]:
                if other != target:
                    self.assertEqual(state["channels"][other], 0.)

    def test_natural_linear_acceleration_ramp_triggers_after_nonquiet_samples(self):
        for sign, target in ((1, "up"), (-1, "down")):
            controller = centered()
            controller.set_mode("flick")
            step(controller, frames=20, armed=True)
            for acceleration in (0., .08, .2, .4, .7, 1.3):
                state = step(controller, raw((0., acceleration * sign, 1.)),
                             armed=True, dt=25)
            self.assertEqual(state["event_count"], 1)
            self.assertEqual(state["last_flick"]["kind"], "linear")
            self.assertGreater(state["channels"][target], .5)

    def test_long_motion_onset_expires_until_another_quiet_interval(self):
        controller = centered()
        controller.set_mode("flick")
        step(controller, frames=20, armed=True)
        angle = 0.
        # Above quiet but below flick for 400 ms: not a rapid gesture onset.
        for speed in [35.] * 16 + [190.]:
            angle += math.radians(speed * .025)
            sample = raw((math.sin(angle), 0., math.cos(angle)), (0., -speed, 0.))
            state = step(controller, sample, armed=True, dt=25)
        self.assertEqual(state["event_count"], 0)
        self.assertFalse(state["flick_ready"])
        self.assertEqual(sum(state["channels"].values()), 0.)
        still = raw((math.sin(angle), 0., math.cos(angle)))
        state = step(controller, still, frames=20, armed=True, dt=25)
        self.assertTrue(state["flick_ready"])
        for speed in (20., 40., 70., 130., 190.):
            angle -= math.radians(speed * .025)
            sample = raw((math.sin(angle), 0., math.cos(angle)), (0., speed, 0.))
            state = step(controller, sample, armed=True, dt=25)
        self.assertEqual(state["event_count"], 1)
        self.assertGreater(state["channels"]["left"], 0.)
        self.assertAlmostEqual(state["last_flick"]["x"],
                               motion._scaled(state["rate_x"], motion.FLICK_DEAD_DPS[0],
                                              motion.FLICK_FULL_DPS[0]))

    def test_release_discards_ready_latch_before_moving_rearm(self):
        controller = centered()
        controller.set_mode("flick")
        self.assertTrue(step(controller, frames=20, armed=True)["flick_ready"])
        state = step(controller, armed=False)
        self.assertFalse(state["flick_ready"])
        state = step(controller, raw(gyro=(0., -190., 0.)), 3, armed=True)
        self.assertFalse(state["flick_ready"])
        self.assertFalse(state["flick_active"])
        self.assertEqual(state["event_count"], 0)
        self.assertEqual(sum(state["channels"].values()), 0.)

    def test_flick_rebound_does_not_fire_opposite_jet(self):
        controller = centered()
        controller.set_mode("flick")
        step(controller, frames=20, armed=True)
        state = step(controller, raw(gyro=(0., -180., 0.)), armed=True)
        first = state["channels"]["right"]
        state = step(controller, raw(gyro=(0., 180., 0.)), 3, armed=True)
        self.assertGreater(state["channels"]["right"], 0.)
        self.assertLess(state["channels"]["right"], first)
        self.assertEqual(state["channels"]["left"], 0.)
        self.assertEqual(state["event_count"], 1)
        state = step(controller, frames=30, armed=True)
        self.assertEqual(sum(state["channels"].values()), 0.)
        state = step(controller, raw(gyro=(0., 180., 0.)), armed=True)
        self.assertAlmostEqual(state["channels"]["left"], first, delta=.005)
        self.assertEqual(state["event_count"], 2)

    def test_vertical_intent_waits_then_emits_one_normal_up_or_down_pulse(self):
        gravity = (0., math.sin(math.radians(70.)), math.cos(math.radians(70.)))
        for sign, target in ((1, "up"), (-1, "down")):
            controller = centered(accel=gravity, vertical_intent_ms=200., **WIDE_RANGE)
            controller.set_mode("flick")
            step(controller, raw_for(controller, gravity), frames=20, armed=True)
            states, angle = vertical_trace(controller, [sign * v for v in (240., 300., 360.)] + [0.] * 8)
            for state in states[:10]:
                self.assertTrue(state["flick_pending"])
                self.assertEqual(state["event_count"], 0)
                self.assertEqual(sum(state["channels"].values()), 0.)
            state = states[10]
            self.assertFalse(state["flick_pending"])
            self.assertEqual(state["event_count"], 1)
            self.assertGreater(state["channels"][target], .85)
            self.assertEqual(sum(state["channels"].values()), state["channels"][target])
            states, angle = vertical_trace(controller, [0.] * 40, angle)
            self.assertEqual(states[-1]["event_count"], 1)
            self.assertEqual(sum(states[-1]["channels"].values()), 0.)

    def test_vertical_preparation_is_replaced_before_any_force_is_applied(self):
        gravity = (0., math.sin(math.radians(70.)), math.cos(math.radians(70.)))
        for sign, target, opposite in ((1, "up", "down"), (-1, "down", "up")):
            controller = centered(accel=gravity, vertical_intent_ms=200., **WIDE_RANGE)
            controller.set_mode("flick")
            step(controller, raw_for(controller, gravity), frames=20, armed=True)
            # The first small reversal is below the replacement threshold; the
            # accelerating final stroke then replaces the unfinished preparation.
            speeds = [-210., -240., -260., -100., 0., 100., 320., 380.] + [0.] * 3
            states, angle = vertical_trace(controller, [speed * sign for speed in speeds])
            for state in states[:10]:
                self.assertEqual(sum(state["channels"].values()), 0.)
                self.assertEqual(state["event_count"], 0)
            self.assertEqual(states[10]["event_count"], 1)
            self.assertGreater(states[10]["channels"][target], .95)
            self.assertEqual(states[10]["channels"][opposite], 0.)
            # A further recoil cannot switch the committed pulse or queue one.
            states, angle = vertical_trace(controller, [-320. * sign] * 4 + [0.] * 35, angle)
            self.assertTrue(all(state["channels"][opposite] == 0. for state in states))
            self.assertTrue(all(state["event_count"] == 1 for state in states))
            self.assertEqual(sum(states[-1]["channels"].values()), 0.)

    def test_small_vertical_rebound_does_not_replace_a_deliberate_stroke(self):
        controller = centered(vertical_intent_ms=200., **WIDE_RANGE)
        controller.set_mode("flick")
        step(controller, frames=20, armed=True)
        states, angle = vertical_trace(controller, [360., 360., -100., -100.] + [0.] * 7, angle=0.)
        self.assertEqual(states[-1]["event_count"], 1)
        self.assertGreater(states[-1]["channels"]["up"], .85)
        self.assertEqual(states[-1]["channels"]["down"], 0.)

    def test_release_discards_vertical_candidate_and_does_not_replay_on_rearm(self):
        controller = centered(vertical_intent_ms=200., **WIDE_RANGE)
        controller.set_mode("flick")
        step(controller, frames=20, armed=True)
        state = step(controller, raw_for(controller, gyro=(-240., 0., 0.)), armed=True)
        self.assertTrue(state["flick_pending"])
        state = step(controller, armed=False)
        self.assertFalse(state["flick_pending"])
        self.assertEqual(state["pending_flick_y"], 0.)
        state = step(controller, raw_for(controller, gyro=(240., 0., 0.)), frames=20, armed=True)
        self.assertEqual(state["event_count"], 0)
        self.assertFalse(state["flick_pending"])
        self.assertEqual(sum(state["channels"].values()), 0.)

    def test_vertical_candidate_is_cleared_by_mode_calibration_or_frame_gap(self):
        for action in ("mode", "calibration", "gap"):
            controller = centered(vertical_intent_ms=200., **WIDE_RANGE)
            controller.set_mode("flick")
            step(controller, frames=20, armed=True)
            self.assertTrue(step(controller, raw_for(controller, gyro=(240., 0., 0.)),
                                 armed=True)["flick_pending"])
            if action == "mode":
                controller.set_mode("tilt")
            elif action == "calibration":
                controller.start_calibration()
            else:
                step(controller, armed=True, dt=151)
            state = controller.snapshot()
            self.assertFalse(state["flick_pending"])
            self.assertEqual(state["event_count"], 0)
            self.assertFalse(state["flick_active"])

    def test_vertical_intent_preserves_immediate_horizontal_and_continuous_tilt(self):
        controller = centered(vertical_intent_ms=200., **WIDE_RANGE)
        controller.set_mode("flick")
        step(controller, frames=20, armed=True)
        state = step(controller, raw_for(controller, gyro=(0., -190., 0.)), armed=True)
        self.assertEqual(state["event_count"], 1)
        self.assertFalse(state["flick_pending"])
        self.assertAlmostEqual(state["channels"]["right"],
                               motion._scaled(190., motion.FLICK_DEAD_DPS[0],
                                              motion.FLICK_FULL_DPS[0]), delta=.0001)
        controller = centered(vertical_intent_ms=200., **WIDE_RANGE)
        step(controller, frames=20, armed=True)
        states, angle = vertical_trace(controller, [240.] * 3, angle=0.)
        self.assertTrue(states[-1]["flick_pending"])
        self.assertEqual(states[-1]["event_count"], 0)
        self.assertGreater(states[-1]["channels"]["up"], .1)
        self.assertEqual(states[-1]["channels"]["up"], states[-1]["tilt_y"])

    def test_release_cancels_pulse_and_rearming_moving_cannot_queue_it(self):
        controller = centered(**WIDE_RANGE)
        controller.set_mode("flick")
        step(controller, frames=20, armed=True)
        state = step(controller, raw_for(controller, gyro=(300., 0., 0.)), armed=True)
        self.assertGreater(state["channels"]["up"], .5)
        state = step(controller, raw_for(controller, gyro=(300., 0., 0.)), armed=False)
        self.assertEqual(sum(state["channels"].values()), 0.)
        self.assertEqual(state["flick_y"], 0.)
        state = step(controller, raw_for(controller, gyro=(300., 0., 0.)), 3, armed=True)
        self.assertEqual(sum(state["channels"].values()), 0.)
        self.assertEqual(state["event_count"], 1)

    def test_translational_flick_is_gravity_removed_and_proportional(self):
        for amount in (1.1, 1.55):
            controller = centered()
            controller.set_mode("flick")
            step(controller, frames=20, armed=True)
            state = step(controller, raw((0., amount, 1.)), armed=True)
            self.assertAlmostEqual(state["linear_y"], amount, delta=.0001)
            self.assertEqual(state["last_flick"]["kind"], "linear")
            self.assertAlmostEqual(state["channels"]["up"],
                                   motion._scaled(amount, motion.LINEAR_DEAD_G[1],
                                                  motion.LINEAR_FULL_G[1]), delta=.0001)
            state = step(controller, frames=40, armed=True)
            self.assertEqual(state["event_count"], 1)
            self.assertEqual(sum(state["channels"].values()), 0.)

    def test_slow_physical_tilt_does_not_emit_flick_or_linear_jet(self):
        controller = centered()
        controller.set_mode("flick")
        step(controller, frames=20, armed=True)
        # Gyro and accelerometer agree during a 30-degree one-second tilt.
        for index in range(1, 51):
            angle = math.radians(.6 * index)
            state = step(controller, raw((math.sin(angle), 0., math.cos(angle)),
                                         (0., -30., 0.)), armed=True)
            self.assertEqual(sum(state["channels"].values()), 0.)
            self.assertLess(abs(state["linear_x"]), .01)
        self.assertEqual(state["event_count"], 0)
        self.assertGreater(state["tilt_x"], .99)
        state = step(controller, raw((.5, 0., math.sqrt(.75))), 100, armed=True)
        self.assertEqual(state["event_count"], 0)
        self.assertEqual(sum(state["channels"].values()), 0.)

    def test_modes_separate_tilt_from_pulse_and_changes_clear_pending_pulse(self):
        controller = centered()
        controller.set_mode("tilt")
        step(controller, frames=20, armed=True)
        state = step(controller, raw(gyro=(0., -190., 0.)), armed=True)
        self.assertAlmostEqual(state["flick_x"],
                               motion._scaled(190., motion.FLICK_DEAD_DPS[0],
                                              motion.FLICK_FULL_DPS[0]), delta=.0001)
        self.assertEqual(state["channels"]["right"], 0.)
        controller.set_mode("flick")
        self.assertEqual(controller.snapshot()["flick_x"], 0.)
        state = step(controller, raw((.5, 0., math.sqrt(.75))), 150, armed=True)
        self.assertGreater(state["tilt_x"], .99)
        self.assertEqual(sum(state["channels"].values()), 0.)
        controller.set_mode("both")
        state = step(controller, raw((.5, 0., math.sqrt(.75))), armed=True)
        self.assertGreater(state["channels"]["right"], .99)

    def test_long_or_nonpositive_frame_gaps_clear_output_and_do_not_integrate(self):
        for dt in (0, -10, 151, 5000):
            controller = centered()
            controller.set_mode("flick")
            step(controller, frames=20, armed=True)
            step(controller, raw(gyro=(0., -190., 0.)), armed=True)
            state = step(controller, raw(gyro=(0., -190., 0.)), armed=True, dt=dt)
            self.assertEqual(sum(state["channels"].values()), 0.)
            self.assertEqual(state["armed"], dt > 150)
            self.assertEqual(state["tilt_valid"], dt > 150)
            self.assertEqual(state["flick_x"], 0.)
            state = step(controller, raw(gyro=(0., -190., 0.)), armed=True)
            self.assertEqual(sum(state["channels"].values()), 0.)

    def test_output_snapshot_cannot_mutate_controller(self):
        controller = centered()
        state = controller.snapshot()
        state["channels"]["right"] = 1.
        state["last_flick"]["x"] = 1.
        self.assertEqual(controller.channels["right"], 0.)
        self.assertEqual(controller.last_flick["x"], 0.)

    def test_invalid_configuration_is_explicit(self):
        controller = centered()
        for axes in ((0, 1, 0, 1), (3, 1, 1, 1), (0, 0, 1, 1)):
            with self.assertRaises(ValueError):
                controller.set_axes(*axes)
        with self.assertRaises(ValueError):
            controller.set_mode("backflip")
        for value in (0., -1., float('inf'), float('nan')):
            for key in ("accel_scale", "gyro_scale"):
                with self.assertRaises(ValueError):
                    motion.MotionController(**{key: value})
        for value in (-1., 351., float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                motion.MotionController(vertical_intent_ms=value)


if __name__ == "__main__":
    unittest.main()
