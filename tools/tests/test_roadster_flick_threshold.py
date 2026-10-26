"""Deliberate flick policy under physically consistent wrist/accel traces.

These checks exercise the standalone sensor controller; native game tests
separately prove its recognized event commits and completes a physical flip.
"""
import importlib.util
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


motion = load("roadster_deliberate_motion", ROOT / "apps/roadster_sandbox/motion.py")
CADENCES = (20., 25., 50., 100.)
GRIPS = ((0., 0., 1.), (0., math.sin(math.radians(70.)), math.cos(math.radians(70.))))


def raw(accel, gyro=(0., 0., 0.)):
    return tuple(round(v * 8192.) for v in accel) + tuple(round(v / .035) for v in gyro)


def centered(grip, dt, module=motion):
    controller = module.MotionController(accel_scale=1. / 8192., gyro_scale=.035,
                                         vertical_intent_ms=200.)
    for _ in range(math.ceil(1100. / dt)):
        controller.update(raw(grip), dt, False)
    assert controller.calibrated
    for _ in range(math.ceil(400. / dt)):
        controller.update(raw(grip), dt, True)
    assert controller.snapshot()["flick_ready"]
    return controller


def rotation_sample(controller, axis, angle, rate):
    """Gravity and gyro for rotation relative to the calibrated playing grip."""
    basis = controller.basis_x if axis == 0 else controller.basis_y
    n = controller.neutral
    a = math.radians(angle)
    gravity = tuple(n[i] * math.cos(a) + basis[i] * math.sin(a) for i in range(3))
    tangent = tuple(-n[i] * math.sin(a) + basis[i] * math.cos(a) for i in range(3))
    gyro = tuple(v * rate for v in motion._cross(tangent, gravity))
    return raw(gravity, gyro)


def wrist_trace(controller, axis, sign, peak, dt):
    """A 240 ms acceleration/deceleration, then held still at its endpoint."""
    states, angle, elapsed = [], 0., 0.
    while elapsed < 240.:
        midpoint = min(240., elapsed + dt * .5)
        rate = peak * max(0., 1. - abs(midpoint - 120.) / 120.) * sign
        angle += rate * dt / 1000.
        states.append(controller.update(rotation_sample(controller, axis, angle, rate), dt, True))
        elapsed += dt
    for _ in range(math.ceil(450. / dt)):
        states.append(controller.update(rotation_sample(controller, axis, angle, 0.), dt, True))
    return states


class DeliberateRoadsterFlickTests(unittest.TestCase):
    def test_ordinary_quick_tilts_remain_manual(self):
        for grip in GRIPS:
            for dt in CADENCES:
                for axis, peak in ((0, 140.), (1, 150.)):
                    for sign in (-1, 1):
                        with self.subTest(grip=grip, dt=dt, axis=axis, sign=sign):
                            controller = centered(grip, dt)
                            states = wrist_trace(controller, axis, sign, peak, dt)
                            self.assertTrue(all(s["flick_count"] == 0 for s in states))
                            self.assertTrue(all(not s["flick_pending"] for s in states))
                            self.assertTrue(all(s["tilt_valid"] for s in states))
                            self.assertGreater(abs(states[-1]["tilt_x" if axis == 0 else "tilt_y"]), .1)

    def test_deliberate_angular_flicks_still_register_once_in_every_direction(self):
        for grip in GRIPS:
            for dt in CADENCES:
                for axis, peak in ((0, 320.), (1, 320.)):
                    for sign in (-1, 1):
                        with self.subTest(grip=grip, dt=dt, axis=axis, sign=sign):
                            states = wrist_trace(centered(grip, dt), axis, sign, peak, dt)
                            self.assertEqual(states[-1]["flick_count"], 1)
                            self.assertGreater(states[-1]["last_flick"]["x" if axis == 0 else "y"] * sign, 0.)
                            self.assertEqual(states[-1]["last_flick"]["kind"], "angular")
                            self.assertFalse(states[-1]["flick_active"])

    def test_linear_acceleration_has_firmer_threshold_in_each_direction(self):
        for grip in GRIPS:
            for dt in CADENCES:
                for axis, weak, strong in ((0, .60, 1.05), (1, .60, 1.10)):
                    for sign in (-1, 1):
                        for magnitude, expected in ((weak, 0), (strong, 1)):
                            with self.subTest(grip=grip, dt=dt, axis=axis, sign=sign,
                                              acceleration=magnitude):
                                controller = centered(grip, dt)
                                basis = controller.basis_x if axis == 0 else controller.basis_y
                                accel = tuple(grip[i] + basis[i] * magnitude * sign for i in range(3))
                                states = [controller.update(raw(accel), dt, True)]
                                for _ in range(math.ceil(450. / dt)):
                                    states.append(controller.update(raw(grip), dt, True))
                                state = states[-1]
                                self.assertEqual(state["flick_count"], expected)
                                if not expected:
                                    self.assertTrue(all(not sample["flick_pending"] for sample in states))
                                    self.assertTrue(all(not sample["flick_active"] for sample in states))
                                if expected:
                                    self.assertGreater(state["last_flick"]["x" if axis == 0 else "y"] * sign, 0.)
                                    self.assertEqual(state["last_flick"]["kind"], "linear")

    def test_deliberate_vertical_stroke_still_registers_above_firmer_onset(self):
        # The same consistent wrist motion is accepted in either direction
        # across normal frame rates and both flat/face-facing grips.
        for grip in GRIPS:
            for dt in CADENCES:
                for sign in (-1, 1):
                    with self.subTest(grip=grip, dt=dt, sign=sign):
                        controller = centered(grip, dt)
                        angle = 240. * sign * dt / 1000.
                        state = controller.update(rotation_sample(controller, 1, angle, 240. * sign), dt, True)
                        self.assertTrue(state["flick_pending"])
                        self.assertEqual(state["flick_count"], 0)
                        for _ in range(math.ceil(450. / dt)):
                            state = controller.update(rotation_sample(controller, 1, angle, 0.), dt, True)
                        self.assertEqual(state["flick_count"], 1)
                        self.assertGreater(state["last_flick"]["y"] * sign, .3)
                        self.assertEqual(state["last_flick"]["kind"], "angular")

    def test_horizontal_trigger_and_strength_keep_the_accepted_feel(self):
        # Independent numeric baselines protect the user's accepted left/right
        # tuning while forward/back detection changes.
        for sign in (-1, 1):
            for rate, expected in ((150., 0.), (200., 40. / 340.), (500., 1.)):
                with self.subTest(sign=sign, rate=rate):
                    controller = centered(GRIPS[0], 25.)
                    state = controller.update(rotation_sample(controller, 0, rate * sign * .025, rate * sign), 25., True)
                    self.assertEqual(state["flick_count"], int(expected > 0.))
                    self.assertFalse(state["flick_pending"])
                    self.assertAlmostEqual(state["flick_x"] * sign, expected, delta=.0001)
            for acceleration, expected in ((.7, 0.), (1.05, .3 / .85), (1.6, 1.)):
                with self.subTest(sign=sign, acceleration=acceleration):
                    controller = centered(GRIPS[0], 25.)
                    state = controller.update(raw((acceleration * sign, 0., 1.)), 25., True)
                    self.assertEqual(state["flick_count"], int(expected > 0.))
                    self.assertFalse(state["flick_pending"])
                    self.assertAlmostEqual(state["flick_x"] * sign, expected, delta=.0001)

    def test_vertical_preparation_still_chooses_the_final_stronger_stroke(self):
        for dt in (20., 25., 50.):
            for sign in (-1, 1):
                with self.subTest(dt=dt, sign=sign):
                    controller = centered(GRIPS[1], dt)
                    angle, states = 0., []
                    # Move down before the upward stroke, as a face-facing
                    # player does. All force waits for intent resolution.
                    for rate in (-210., -240., 320., 380., 0., 0., 0., 0., 0., 0., 0., 0.):
                        rate *= sign
                        angle += rate * dt / 1000.
                        states.append(controller.update(rotation_sample(controller, 1, angle, rate), dt, True))
                    self.assertEqual(states[-1]["flick_count"], 1)
                    self.assertGreater(states[-1]["last_flick"]["y"] * sign, .95)
                    self.assertTrue(all(not s["flick_count"] or s["last_flick"]["y"] * sign > 0.
                                        for s in states))


if __name__ == "__main__":
    unittest.main()
