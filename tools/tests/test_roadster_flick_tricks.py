"""Committed gesture trajectories against the actual portable rigid-body engine.

Host checks only; the native-runtime and physical input suites have separate
receipts. All scenario pose/rate assignments here happen before a controller
starts, never inside the deployed controller.
"""
import math
import unittest

from tools.tests.test_roadster_tricks import p, t, state

DIRECTIONS = ('left', 'right', 'back', 'forward')


def make_car(height=10., hz=40, limited=False, reserve=1.):
    sim = p.Simulation('flat')
    sim.set_step_hz(hz)
    if not limited:
        sim.set_air_mode('unlimited')
    sim.y = height
    sim.airborne = height > .6
    sim.air_mass = p.TANK_RESIDUAL+reserve*(p.TANK_MASS-p.TANK_RESIDUAL)
    sim.mass = p.DRY_MASS+sim.air_mass
    sim.air_fraction = reserve
    return sim


def finish(sim, controller, controls=None, frame_dt=None):
    peak, inverted, signed_turn = sim.y, False, 0.
    side_forces = [0., 0.]
    axis, sign = controller._flick_axis, controller._flick_direction
    initial_time = sim.time
    previous = p.q_inverse_rotate(sim.q, sim.omega)[axis]

    def observe(dt, car, _values):
        nonlocal signed_turn, previous
        rate = p.q_inverse_rotate(car.q, car.omega)[axis]
        signed_turn += .5*(previous+rate)*dt
        previous = rate

    while controller.active and sim.time-initial_time < 8.1:
        sim.step(frame_dt or sim.fixed_dt, controls or {}, controller.update, observe)
        peak = max(peak, sim.y)
        inverted = inverted or sim.basis()[1][1] < -.9
        for i in range(2):
            side_forces[i] = max(side_forces[i], sim.nozzle_forces[i+8])
    return peak, inverted, signed_turn*sign, side_forces


class CommittedFlickTests(unittest.TestCase):
    def assert_finished(self, sim, controller):
        self.assertFalse(controller.active, controller.message)
        self.assertFalse(controller.committed)
        self.assertIn('complete', controller.message)
        self.assertGreater(sim.basis()[1][1], .999)
        body_rate = p.q_inverse_rotate(sim.q, sim.omega)
        self.assertLess(abs(body_rate[0]), .15)
        self.assertLess(abs(body_rate[2]), .15)
        self.assertEqual(sim.nozzle_forces, [0.]*10)
        self.assertTrue(sim.airborne)

    def test_four_neutral_gestures_complete_a_full_physical_rotation(self):
        for hz in (40, 120):
            for direction in DIRECTIONS:
                with self.subTest(hz=hz, direction=direction):
                    sim, controller = make_car(hz=hz), t.TrickController()
                    before = state(sim)
                    self.assertTrue(controller.start_flick(direction, sim))
                    self.assertEqual(state(sim), before)
                    peak, inverted, turn, sides = finish(sim, controller)
                    self.assert_finished(sim, controller)
                    self.assertTrue(inverted)
                    self.assertAlmostEqual(controller.rotation, 2.*math.pi, delta=.03)
                    self.assertAlmostEqual(turn, 2.*math.pi, delta=.04)
                    self.assertLess(sim.time, 2.5)
                    self.assertLess(peak, 15.)
                    if direction in ('left', 'right'):
                        self.assertTrue(all(force > 100. for force in sides))

    def test_ground_launch_gets_clearance_before_rotating(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                sim, controller = make_car(.59), t.TrickController()
                self.assertTrue(controller.start_flick(direction, sim))
                output = controller.update(sim.fixed_dt, sim, {'fan': 0.})
                self.assertEqual(output['nozzles'][4:8], [1.]*4)
                peak, inverted, turn, _ = finish(sim, controller)
                self.assert_finished(sim, controller)
                self.assertTrue(inverted)
                self.assertAlmostEqual(turn, 2.*math.pi, delta=.04)
                self.assertLess(peak, 13.)

    def test_high_hover_does_not_repeat_the_full_launch(self):
        for direction in DIRECTIONS:
            with self.subTest(direction=direction):
                sim, controller = make_car(16.), t.TrickController()
                self.assertTrue(controller.start_flick(direction, sim))
                peak, _, _, _ = finish(sim, controller)
                self.assert_finished(sim, controller)
                self.assertLess(peak, 17.5)
                self.assertLess(sim.time, 2.)

    def test_moderate_prior_lean_and_rates_are_accepted_and_leveled(self):
        for direction in DIRECTIONS:
            for lean_sign in (-1., 1.):
                with self.subTest(direction=direction, lean_sign=lean_sign):
                    sim, controller = make_car(8.), t.TrickController()
                    axis = 2 if direction in ('left', 'right') else 0
                    vector = (0., 0., 1.) if axis == 2 else (1., 0., 0.)
                    sim.q = p.q_axis(vector, .7*lean_sign)
                    sim.omega[axis] = 3.*lean_sign
                    sim.omega[0 if axis == 2 else 2] = lean_sign
                    self.assertTrue(controller.start_flick(direction, sim))
                    peak, inverted, _, _ = finish(sim, controller)
                    self.assert_finished(sim, controller)
                    self.assertTrue(inverted)
                    self.assertLess(peak, 16.)
                    self.assertLess(sim.time, 3.5)

    def test_repeated_events_cannot_restart_or_reverse_an_active_commitment(self):
        sim, controller = make_car(), t.TrickController()
        controller.start_flick('left', sim)
        sim.step(.5, {}, controller.update)
        original = (controller.active, controller.phase, controller.rotation,
                    controller._start_time, controller._flick_direction)
        before = state(sim)
        for direction in DIRECTIONS:
            self.assertFalse(controller.start_flick(direction, sim))
            self.assertEqual(original, (controller.active, controller.phase,
                             controller.rotation, controller._start_time,
                             controller._flick_direction))
            self.assertEqual(state(sim), before)
        finish(sim, controller)
        self.assert_finished(sim, controller)

    def test_second_deliberate_flick_can_complete_after_the_first(self):
        sim, controller = make_car(), t.TrickController()
        for direction in ('right', 'back', 'left', 'forward'):
            self.assertTrue(controller.start_flick(direction, sim))
            finish(sim, controller)
            self.assert_finished(sim, controller)
            self.assertAlmostEqual(controller.rotation, 2.*math.pi, delta=.07)

    def test_landing_then_another_flick_needs_no_reset_or_refill(self):
        sim, controller = make_car(.59), t.TrickController()
        for direction in DIRECTIONS:
            self.assertTrue(controller.start_flick(direction, sim))
            finish(sim, controller)
            self.assert_finished(sim, controller)
            released_at = sim.time
            while (sim.airborne or abs(sim.vy) > .1) and sim.time-released_at < 5.:
                sim.step(sim.fixed_dt, {}, controller.update)
            self.assertFalse(sim.airborne)
            self.assertGreater(sim.basis()[1][1], .95)
            self.assertEqual(sim.air_fraction, 1.)

    def test_manual_nozzles_cancel_and_pass_through_without_pose_changes(self):
        sim, controller = make_car(), t.TrickController()
        controller.start_flick('back', sim)
        sim.step(.5, {}, controller.update)
        before = state(sim)
        controls = {'fan': 1., 'nozzles': [.3]+[0.]*9}
        self.assertEqual(controller.update(sim.fixed_dt, sim, controls), controls)
        self.assertFalse(controller.committed)
        self.assertFalse(controller.active)
        self.assertEqual(state(sim), before)

    def test_explicit_cancel_preserves_free_rotation_and_gravity(self):
        sim, controller = make_car(), t.TrickController()
        controller.start_flick('right', sim)
        while controller.rotation < 1.:
            sim.step(sim.fixed_dt, {}, controller.update)
        before = state(sim)
        controller.cancel()
        self.assertEqual(state(sim), before)
        self.assertFalse(controller.committed)
        sim.step(.1, {}, controller.update)
        self.assertNotEqual(sim.q, before[6:10])
        self.assertEqual(sim.nozzle_forces, [0.]*10)
        self.assertGreater(sim.omega[2], 1.)

    def test_contact_ends_commitment_without_righting_the_car(self):
        sim, controller = make_car(), t.TrickController()
        controller.start_flick('left', sim)
        while controller.rotation < 2.:
            sim.step(sim.fixed_dt, {}, controller.update)
        sim.airborne = False
        before = state(sim)
        output = controller.update(sim.fixed_dt, sim, {})
        self.assertEqual(state(sim), before)
        self.assertFalse(controller.committed)
        self.assertIn('contact', controller.message)
        self.assertEqual(output['nozzles'], [0.]*10)

    def test_clock_reset_releases_an_active_commitment(self):
        sim, controller = make_car(), t.TrickController()
        controller.start_flick('back', sim)
        sim.step(.5, {}, controller.update)
        sim.reset()
        before = state(sim)
        self.assertEqual(controller.update(sim.fixed_dt, sim, {}), {})
        self.assertFalse(controller.committed)
        self.assertEqual(state(sim), before)

    def test_limited_air_completes_at_the_accepted_reserve(self):
        for height in (.59, 10.):
            for direction in DIRECTIONS:
                with self.subTest(height=height, direction=direction):
                    sim, controller = make_car(height, limited=True, reserve=.8), t.TrickController()
                    self.assertTrue(controller.start_flick(direction, sim))
                    finish(sim, controller)
                    self.assert_finished(sim, controller)
                    self.assertLess(sim.air_fraction, .8)
                    self.assertGreater(sim.air_mass, p.TANK_RESIDUAL)

    def test_insufficient_reserve_is_rejected_without_firing_or_changing_motion(self):
        sim, controller = make_car(limited=True, reserve=.79), t.TrickController()
        before = state(sim)
        self.assertFalse(controller.start_flick('forward', sim))
        self.assertFalse(controller.committed)
        self.assertEqual(state(sim), before)
        self.assertEqual(controller.update(sim.fixed_dt, sim, {}), {})
        self.assertIn('air', controller.message)

    def test_pressure_exhaustion_releases_force_and_keeps_falling(self):
        sim, controller = make_car(limited=True), t.TrickController()
        controller.start_flick('forward', sim)
        sim.step(.5, {}, controller.update)
        sim.air_mass = p.TANK_RESIDUAL
        sim.mass = p.DRY_MASS+sim.air_mass
        before = state(sim)
        sim.step(.1, {}, controller.update)
        self.assertFalse(controller.committed)
        self.assertIn('Pressure exhausted', controller.message)
        self.assertEqual(sim.nozzle_forces, [0.]*10)
        self.assertNotEqual(state(sim), before)

    def test_airborne_fan_and_driving_commands_remain_independent(self):
        sim, controller = make_car(), t.TrickController()
        controller.start_flick('left', sim)
        controls = {'throttle': .3, 'steer': -.5, 'fan': 1.}
        output = controller.update(sim.fixed_dt, sim, controls)
        for key, value in controls.items():
            self.assertEqual(output[key], value)
        finish(sim, controller, controls)
        self.assert_finished(sim, controller)
        self.assertGreater(sim.fan_speed, .9)

    def test_grounded_fan_does_not_turn_timeout_into_a_ground_roll(self):
        sim, controller = make_car(.59), t.TrickController()
        for _ in range(40):
            sim.step(.025, {'fan': 1.})
        self.assertTrue(controller.start_flick('right', sim))
        finish(sim, controller, {'fan': 1.})
        self.assertFalse(controller.committed)
        # Fan-loaded suspension may briefly unload then regain contact. Both
        # a retained contact and that real landing must release the sequence.
        self.assertIn('contact', controller.message)
        self.assertLess(controller.rotation, .01)
        self.assertGreater(sim.basis()[1][1], .999)
        self.assertGreater(sim.fan_speed, .9)

    def test_update_only_produces_bounded_commands_and_never_mutates_physics(self):
        sim, controller = make_car(), t.TrickController()
        controller.start_flick('right', sim)
        while controller.active:
            before = state(sim)
            output = controller.update(sim.fixed_dt, sim, {})
            self.assertEqual(state(sim), before)
            self.assertTrue(all(0. <= value <= 1. for value in output['nozzles']))
            sim.step(sim.fixed_dt, output)
            self.assertLess(sim.time, 4.)

    def test_fixed_step_flicks_are_independent_of_render_frequency(self):
        for direction in DIRECTIONS:
            reference = None
            for fps in (10, 20, 40, 120):
                sim, controller = make_car(), t.TrickController()
                controller.start_flick(direction, sim)
                for _ in range(4*fps):
                    sim.step(1./fps, {}, controller.update)
                self.assertIn('complete', controller.message)
                if reference is None:
                    reference = state(sim)
                else:
                    for actual, expected in zip(state(sim), reference):
                        self.assertAlmostEqual(actual, expected, delta=1.e-8)


if __name__ == '__main__':
    unittest.main()
