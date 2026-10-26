"""Read-only stunt feedback, checked against real sandbox trajectories."""
import importlib.util
import math
from pathlib import Path
import sys
import unittest


APP = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, APP / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


p = load('stunt_feedback_test_physics', 'physics.py')
f = load('stunt_feedback_test_module', 'stunt_feedback.py')
old_physics = sys.modules.get('physics')
sys.modules['physics'] = p
try:
    tricks = load('stunt_feedback_test_tricks', 'tricks.py')
finally:
    if old_physics is None:
        del sys.modules['physics']
    else:
        sys.modules['physics'] = old_physics

LIFT = {'nozzles': [0.] * 4 + [1.] * 4 + [0.] * 2}


def setup(hz=120):
    sim = p.Simulation('flat')
    sim.set_step_hz(hz)
    sim.set_air_mode('unlimited')
    feedback = f.StuntFeedback()
    feedback.reset(sim)
    return sim, feedback


def tick_pose(sim, feedback, dt=1./120, airborne=True):
    """Orientation fixture: feed observed poses without applying commands."""
    sim.time += dt
    sim.airborne = airborne
    sim.contact_count = 0 if airborne else 4
    sim.chassis_contacts = 0
    sim.omega = [0., 0., 0.]
    sim.vy = 0.
    feedback.update(dt, sim, None)


def rotate(sim, feedback, axis, angle, sign_changes=False):
    # Eight degrees per tick stays safely below quaternion aliasing. The world
    # axis follows the body, so an already tilted car can still yaw locally.
    count = int(math.ceil(abs(angle)/(math.pi/24)))
    for index in range(count):
        world_axis = p.q_rotate(sim.q, axis)
        sim.q = p.q_advance(sim.q, world_axis, angle/count)
        if sign_changes and index % 2:
            sim.q = tuple(-v for v in sim.q)
        tick_pose(sim, feedback)


class StuntFeedbackTests(unittest.TestCase):
    def test_repeated_real_launches_measure_air_ticks_and_natural_landings(self):
        for hz in (60, 120):
            with self.subTest(hz=hz):
                sim, feedback = setup(hz)
                results = []
                for jump in range(2):
                    measured_air = 0.
                    for index in range(hz*14):
                        sim.step(1./hz, LIFT if index < round(.9*hz) else {},
                                 observer=feedback.update)
                        if sim.airborne:
                            measured_air += 1./hz
                        if measured_air and feedback.message.startswith('LANDED'):
                            break
                    else:
                        self.fail('Natural upright landing never confirmed')
                    state = feedback.snapshot()
                    self.assertFalse(state['airborne'])
                    self.assertEqual(sim.contact_count, 4)
                    self.assertGreater(sim.basis()[1][1], .99)
                    self.assertAlmostEqual(state['airtime'], measured_air, places=8)
                    self.assertGreater(state['peak_height'], 20.)
                    self.assertEqual((state['rolls'], state['flips']), (0, 0))
                    self.assertEqual(state['height'], 0.)
                    self.assertLessEqual(len(state['message']), 24)
                    results.append(state['airtime'])
                    # A new takeoff must replace the old message and record.
                    for _ in range(hz):
                        sim.step(1./hz, {}, observer=feedback.update)
                self.assertLess(abs(results[1]-results[0]), 1.)
                self.assertEqual(sim.air_fraction, 1.)

    def test_real_force_driven_roll_and_backflip_but_not_yaw_spin(self):
        expected = {'hop': (0, 0, None), 'kickflip': (1, 0, 'ROLL RIGHT'),
                    'backflip': (0, 1, 'BACKFLIP'), 'spin': (0, 0, None)}
        for name, (rolls, flips, callout) in expected.items():
            with self.subTest(name=name):
                sim, feedback = setup()
                sim.set_air_mode('limited')
                controller = tricks.TrickController()
                self.assertTrue(controller.start(name, sim))
                messages = set()
                for _ in range(120*9):
                    sim.step(1./120, {}, controller.update, feedback.update)
                    messages.add(feedback.message)
                self.assertEqual(feedback.rolls, rolls)
                self.assertEqual(feedback.flips, flips)
                self.assertTrue(any(m.startswith('LANDED') for m in messages))
                if callout:
                    self.assertIn(callout, messages)
                self.assertGreater(sim.time, 8.)

    def test_partial_reversals_do_not_accumulate_false_rotations(self):
        for axis in ((1., 0., 0.), (0., 0., 1.)):
            sim, feedback = setup()
            sim.y = 10.
            tick_pose(sim, feedback)
            for _ in range(5):
                rotate(sim, feedback, axis, math.radians(300))
                rotate(sim, feedback, axis, -math.radians(300))
            self.assertEqual(feedback.rolls, 0)
            self.assertEqual(feedback.flips, 0)
            self.assertEqual(feedback.message, '')

    def test_completed_opposite_turns_both_count_without_direction_mislabel(self):
        sim, feedback = setup()
        tick_pose(sim, feedback)
        rotate(sim, feedback, (1., 0., 0.), 2.*math.pi)
        self.assertEqual(feedback.flips, 1)
        self.assertEqual(feedback.message, 'FRONTFLIP')
        rotate(sim, feedback, (1., 0., 0.), -2.*math.pi)
        self.assertEqual(feedback.flips, 2)
        self.assertEqual(feedback.message, 'BACKFLIP')
        self.assertEqual(feedback.rolls, 0)

    def test_yaw_and_quaternion_sign_changes_do_not_award_flips(self):
        sim, feedback = setup()
        sim.q = p.q_axis((1., 0., 0.), .8)
        feedback.reset(sim)
        tick_pose(sim, feedback)
        rotate(sim, feedback, (0., 1., 0.), 8.*math.pi, True)
        self.assertEqual((feedback.rolls, feedback.flips), (0, 0))
        rotate(sim, feedback, (0., 0., 1.), -2.*math.pi, True)
        self.assertEqual((feedback.rolls, feedback.flips), (1, 0))
        self.assertEqual(feedback.message, 'ROLL LEFT')

    def test_a_wheel_brush_is_not_a_landing_and_ground_ticks_are_not_airtime(self):
        sim, feedback = setup()
        sim.y = 4.
        for _ in range(60):
            tick_pose(sim, feedback)
        for _ in range(12):
            tick_pose(sim, feedback, airborne=False)
        self.assertEqual(feedback.message, '')
        self.assertAlmostEqual(feedback.airtime, .5)
        for _ in range(24):
            tick_pose(sim, feedback)
        for _ in range(23):
            tick_pose(sim, feedback, airborne=False)
        self.assertEqual(feedback.message, '')
        tick_pose(sim, feedback, airborne=False)
        self.assertEqual(feedback.message, 'LANDED 0.7S AIR')
        self.assertAlmostEqual(feedback.airtime, .7)

    def test_natural_inverted_crash_has_no_landing_award_or_pose_reset(self):
        sim, feedback = setup(60)
        sim.y = 5.
        sim.q = p.q_axis((0., 0., 1.), math.pi)
        messages = []
        for _ in range(60*7):
            sim.step(1./60, {}, observer=feedback.update)
            messages.append(feedback.message)
        self.assertGreater(sim.chassis_contacts, 0)
        self.assertLess(sim.basis()[1][1], -.99)
        self.assertEqual(sim.time, 7.)
        self.assertFalse(any(m.startswith('LANDED') for m in messages))
        self.assertFalse(feedback.airborne)
        self.assertGreater(feedback.airtime, .5)

    def test_reset_clears_partial_rotations_and_midair_reset_waits_for_support(self):
        sim, feedback = setup()
        tick_pose(sim, feedback)
        rotate(sim, feedback, (1., 0., 0.), math.radians(300))
        feedback.reset(sim)
        rotate(sim, feedback, (1., 0., 0.), 4.*math.pi)
        self.assertEqual(feedback.airtime, 0.)
        self.assertEqual(feedback.flips, 0)
        self.assertEqual(feedback.message, '')
        sim.reset('flat')
        # Resetting only the physics is defended too: the clock drops.
        sim.step(sim.fixed_dt, {}, observer=feedback.update)
        self.assertEqual(feedback.airtime, 0.)
        self.assertEqual(feedback.flips, 0)
        self.assertEqual(feedback.peak_height, 0.)
        self.assertEqual(feedback.message, '')
        for _ in range(60):
            sim.step(sim.fixed_dt, {}, observer=feedback.update)
        tick_pose(sim, feedback)
        rotate(sim, feedback, (1., 0., 0.), math.radians(90))
        self.assertEqual(feedback.flips, 0)

    def test_observer_preserves_entire_simulation_state_through_flight_and_crash(self):
        plain, _ = setup(60)
        observed, feedback = setup(60)
        for frame in range(600):
            controls = LIFT if frame < 30 else {}
            if 35 <= frame < 45:
                controls = {'nozzles': [0.] * 4 + [1., 1., 0., 0.]}
            if frame > 400:
                controls = {'fan': 1., 'throttle': .5, 'steer': -.3,
                            'nozzles': [1.] * 4 + [0.] * 6}
            plain.step(1./30, controls)
            observed.step(1./30, controls, observer=feedback.update)
        self.assertEqual(plain.__dict__, observed.__dict__)

    def test_render_schedule_does_not_change_measurement_or_message_expiry(self):
        outcomes = []
        for frame_dt in (1./15, 1./30, 1./60):
            sim, feedback = setup()
            controller = tricks.TrickController()
            controller.start('hop', sim)
            for _ in range(round(8./frame_dt)):
                sim.step(frame_dt, {}, controller.update, feedback.update)
            outcomes.append(feedback.snapshot())
            self.assertEqual(feedback.message, '')
            self.assertGreater(feedback.airtime, 1.)
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[1], outcomes[2])


if __name__ == '__main__':
    unittest.main()
