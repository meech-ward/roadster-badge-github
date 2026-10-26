"""Clicky rotation feel measured through actual forces in the portable engine.

These are host physics checks, not MicroPython or physical-button evidence.
Angle measurements integrate the actual body angular velocity; the tests never
give the controller an attitude target or repair a simulated rotation.
"""
import importlib.util
import math
from pathlib import Path
import sys
import unittest


APP = Path(__file__).resolve().parents[2] / 'apps' / 'roadster_sandbox'
SPEC = importlib.util.spec_from_file_location('rotation_test_physics', APP / 'physics.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
SPEC = importlib.util.spec_from_file_location('rotation_test_controls', APP / 'badge_controls.py')
b = importlib.util.module_from_spec(SPEC)
_previous = sys.modules.get('physics')
sys.modules['physics'] = p
try:
    SPEC.loader.exec_module(b)
finally:
    if _previous is None:
        del sys.modules['physics']
    else:
        sys.modules['physics'] = _previous


def airborne(hz=60):
    sim = p.Simulation('flat')
    sim.set_step_hz(hz)
    sim.set_air_mode('unlimited')
    sim.y = 1000.
    return sim


def rates(sim):
    return p.q_inverse_rotate(sim.q, sim.omega)


def phase(sim, controller, seconds, manual=(), buttons=(), motion=None, fan=0.):
    """Return measured rotation, peak rates, and delivered output for a phase."""
    angles, peaks = [0.] * 3, [0.] * 3
    delivered, duty = [0.] * 10, [0.] * 10
    steps = int(round(seconds * sim.step_hz))
    for _ in range(steps):
        before = rates(sim)
        controls = controller.mix(sim.fixed_dt, sim, buttons=buttons,
                                  motion=motion, fan=fan, manual=manual)
        sim.step(sim.fixed_dt, controls)
        after = rates(sim)
        for axis in range(3):
            angles[axis] += (before[axis] + after[axis]) * .5 * sim.fixed_dt
            peaks[axis] = max(peaks[axis], abs(after[axis]))
        for index in range(10):
            delivered[index] = max(delivered[index], sim.nozzle_forces[index])
            duty[index] = max(duty[index], controls['nozzles'][index])
    return {'angle': angles, 'peak': peaks, 'force': delivered, 'duty': duty}


class RotationControlTests(unittest.TestCase):
    def test_prepared_motion_poll_preserves_each_tick_rate_feedback(self):
        normal, prepared = airborne(), airborne()
        controls = b.RotationControl(), b.RotationControl()
        for buttons, manual, motion in (
                (('BACK',), ('RIGHT',), {'channels': {'right': .3, 'up': .2}}),
                (('BACK',), ('UP',), {'channels': {'left': .4, 'down': .1}}),
                ((), (), {'channels': {'right': 1.}}),
                (('SELECT',), (), {'channels': {'down': .8}})):
            axes = b.rotation_axes(buttons, motion)
            for _ in range(20):
                a = controls[0].mix(normal.fixed_dt, normal, buttons, motion, 1., manual)
                c = controls[1].mix(prepared.fixed_dt, prepared, buttons, motion, 1., manual,
                                    motion_axes=axes)
                self.assertEqual(a, c)
                normal.step(normal.fixed_dt, a)
                prepared.step(prepared.fixed_dt, c)
                self.assertEqual((normal.q, normal.omega), (prepared.q, prepared.omega))

    def test_short_roll_taps_make_small_corrections_and_then_settle(self):
        for direction, sign in (('RIGHT', 1.), ('LEFT', -1.)):
            for seconds in (4 / 60, .1, .15):
                with self.subTest(direction=direction, seconds=seconds):
                    sim, control = airborne(), b.RotationControl()
                    held = phase(sim, control, seconds, (direction,))
                    released = phase(sim, control, 1.2)
                    angle = (held['angle'][2] + released['angle'][2]) * sign
                    self.assertGreater(angle, math.radians(.5))
                    self.assertLess(angle, math.radians(18.))
                    self.assertLess(held['peak'][2], math.radians(65.))
                    self.assertLess(abs(rates(sim)[2]), math.radians(2.5))

    def test_sustained_roll_allows_a_flip_without_runaway_acceleration(self):
        sim, control = airborne(), b.RotationControl()
        held = phase(sim, control, 2., ('RIGHT',))
        self.assertGreater(held['angle'][2], math.radians(240.))
        self.assertLess(held['angle'][2], math.radians(390.))
        self.assertGreater(rates(sim)[2], math.radians(150.))
        self.assertLess(held['peak'][2], math.radians(210.))
        released = phase(sim, control, 1.3)
        self.assertLess(abs(rates(sim)[2]), math.radians(2.5))
        self.assertLess(released['angle'][2], math.radians(80.))

    def test_pitch_is_controllable_in_both_directions(self):
        for direction, sign in (('UP', -1.), ('DOWN', 1.)):
            with self.subTest(direction=direction):
                sim, control = airborne(), b.RotationControl()
                tap = phase(sim, control, .1, (direction,))
                release = phase(sim, control, 1.2)
                total = (tap['angle'][0] + release['angle'][0]) * sign
                self.assertGreater(total, math.radians(.5))
                self.assertLess(total, math.radians(15.))
                self.assertLess(abs(rates(sim)[0]), math.radians(2.5))
                sustained = phase(sim, control, 2., (direction,))
                self.assertGreater(rates(sim)[0] * sign, math.radians(125.))
                self.assertLess(sustained['peak'][0], math.radians(200.))

    def test_release_uses_real_opposing_nozzle_torque(self):
        sim, control = airborne(), b.RotationControl()
        held = phase(sim, control, .3, ('RIGHT',))
        before = rates(sim)[2]
        released = phase(sim, control, .2)
        self.assertGreater(before, 0.)
        self.assertLess(rates(sim)[2], before * .5)
        self.assertGreater(held['force'][4] + held['force'][6],
                           held['force'][5] + held['force'][7])
        self.assertGreater(released['force'][5] + released['force'][7],
                           released['force'][4] + released['force'][6])

    def test_opposite_button_brakes_then_reverses(self):
        sim, control = airborne(), b.RotationControl()
        phase(sim, control, .5, ('RIGHT',))
        self.assertGreater(rates(sim)[2], 0.)
        reversed_phase = phase(sim, control, .9, ('LEFT',))
        self.assertLess(rates(sim)[2], -math.radians(80.))
        self.assertLess(reversed_phase['peak'][2], math.radians(210.))

    def test_simultaneous_opposite_buttons_do_not_start_a_rotation(self):
        sim, control = airborne(), b.RotationControl()
        controls = control.mix(sim.fixed_dt, sim,
                               manual=('LEFT', 'RIGHT', 'UP', 'DOWN'))
        self.assertTrue(all(value == 0. for value in controls['nozzles']))

    def test_controller_never_writes_pose_velocity_or_fuel(self):
        sim, control = airborne(), b.RotationControl()
        sim.q = p.q_axis((0., 0., 1.), .8)
        sim.omega = [.4, -.3, .2]
        before = (sim.x, sim.y, sim.z, sim.vx, sim.vy, sim.vz,
                  tuple(sim.q), tuple(sim.omega), sim.air_mass, sim.time)
        controls = control.mix(sim.fixed_dt, sim, manual=('RIGHT',))
        after = (sim.x, sim.y, sim.z, sim.vx, sim.vy, sim.vz,
                 tuple(sim.q), tuple(sim.omega), sim.air_mass, sim.time)
        self.assertEqual(before, after)
        self.assertGreater(sum(controls['nozzles']), 0.)

    def test_release_stops_at_current_angle_instead_of_righting_car(self):
        sim, control = airborne(), b.RotationControl()
        sim.q = p.q_axis((0., 0., 1.), 1.)
        phase(sim, control, .1, ('RIGHT',))
        phase(sim, control, 1.3)
        up = sim.basis()[1]
        self.assertLess(up[1], .7)
        self.assertGreater(abs(up[0]), .7)
        self.assertLess(abs(rates(sim)[2]), math.radians(2.5))

    def test_idle_and_reset_leave_free_tumbles_untouched(self):
        sim, control = airborne(), b.RotationControl()
        sim.omega = [1., .3, 2.]
        controls = control.mix(sim.fixed_dt, sim)
        self.assertEqual(controls['nozzles'], [0.] * 10)
        phase(sim, control, .1, ('RIGHT',))
        control.reset()
        controls = control.mix(sim.fixed_dt, sim)
        self.assertEqual(controls['nozzles'], [0.] * 10)

    def test_exhausted_air_cannot_magically_brake_a_spin(self):
        sim, control = airborne(), b.RotationControl()
        phase(sim, control, .3, ('RIGHT',))
        before = rates(sim)[2]
        sim.set_air_mode('limited')
        sim.air_mass = p.TANK_RESIDUAL
        sim.mass = p.DRY_MASS + sim.air_mass
        released = phase(sim, control, .3)
        self.assertGreater(sum(released['duty']), 0.)
        self.assertLess(sum(released['force']), 1.e-5)
        self.assertGreater(rates(sim)[2], before * .9)

    def test_release_braking_expires_instead_of_fighting_later_collisions(self):
        sim, control = airborne(), b.RotationControl()
        phase(sim, control, .3, ('RIGHT',))
        sim.set_air_mode('limited')
        sim.air_mass = p.TANK_RESIDUAL
        sim.mass = p.DRY_MASS + sim.air_mass
        phase(sim, control, 1.5)
        self.assertGreater(abs(rates(sim)[2]), math.radians(50.))
        self.assertEqual(control.mix(sim.fixed_dt, sim)['nozzles'], [0.] * 10)

    def test_ground_contact_relinquishes_release_braking(self):
        sim, control = airborne(), b.RotationControl()
        phase(sim, control, .1, ('RIGHT',))
        sim.contact_count = 1
        controls = control.mix(sim.fixed_dt, sim)
        self.assertEqual(controls['nozzles'], [0.] * 10)
        # Loss of that contact must not re-arm old clicky input.
        sim.contact_count = 0
        self.assertEqual(control.mix(sim.fixed_dt, sim)['nozzles'], [0.] * 10)

    def test_grounded_clicky_tap_does_not_launch_or_overturn_the_car(self):
        sim, control = p.Simulation('flat'), b.RotationControl()
        sim.set_step_hz(60)
        sim.set_air_mode('unlimited')
        phase(sim, control, 1.)
        phase(sim, control, .1, ('RIGHT',))
        phase(sim, control, .5)
        self.assertGreater(sim.basis()[1][1], .95)
        self.assertLess(sim.y, .7)
        self.assertEqual(sim.contact_count, 4)

    def test_lift_boost_and_fan_keep_working_while_rotating(self):
        sim, control = airborne(), b.RotationControl()
        result = phase(sim, control, .4, ('RIGHT',), ('SELECT', 'BACK'), fan=1.)
        self.assertGreater(sim.vz, 2.)
        self.assertGreater(sim.vy, 2.)
        self.assertGreater(sum(result['force'][:4]), 15000.)
        self.assertGreater(sum(result['force'][4:8]), 50000.)
        controls = control.mix(sim.fixed_dt, sim, buttons=('SELECT', 'BACK'),
                               fan=1., manual=('RIGHT',))
        self.assertEqual(controls['fan'], 1.)
        self.assertTrue(all(0. <= value <= 1. for value in controls['nozzles']))

    def test_fresh_motion_keeps_existing_direct_jet_behavior(self):
        sim, control = airborne(), b.RotationControl()
        motion = {'calibrated': True, 'armed': True,
                  'channels': {'right': .7, 'up': .4}}
        actual = control.mix(sim.fixed_dt, sim, buttons=('BACK',), motion=motion)
        expected = b.mix_controls(('BACK',), motion)
        self.assertEqual(actual, expected)

    def test_motion_takes_axis_back_after_clicky_release(self):
        sim, control = airborne(), b.RotationControl()
        phase(sim, control, .5, ('RIGHT',))
        motion = {'calibrated': True, 'armed': True, 'channels': {'right': 1.}}
        actual = control.mix(sim.fixed_dt, sim, buttons=('BACK',), motion=motion)
        expected = b.mix_controls(('BACK',), motion)
        self.assertEqual(actual['nozzles'], expected['nozzles'])

    def test_two_axes_remain_bounded_and_direct_yaw_is_cancelled(self):
        sim, control = airborne(), b.RotationControl()
        for _ in range(90):
            controls = control.mix(sim.fixed_dt, sim, manual=('RIGHT', 'UP'))
            torque = [0.] * 3
            for duty, nozzle in zip(controls['nozzles'], p.NOZZLES):
                self.assertGreaterEqual(duty, 0.)
                self.assertLessEqual(duty, 1.)
                moment = p.cross(nozzle[1], p.scale(nozzle[2], nozzle[3] * duty))
                for axis in range(3):
                    torque[axis] += moment[axis]
            self.assertAlmostEqual(torque[1], 0., places=7)
            sim.step(sim.fixed_dt, controls)
            self.assertTrue(all(math.isfinite(value) for value in sim.q + tuple(sim.omega)))
            self.assertLess(abs(rates(sim)[2]), math.radians(240.))
            self.assertLess(abs(rates(sim)[0]), math.radians(240.))

    def test_fixed_tick_control_is_independent_of_render_cadence(self):
        outcomes = []
        for render_hz in (15, 30, 60):
            sim, control = airborne(), b.RotationControl()

            def per_tick(dt, current, unused):
                manual = ('RIGHT',) if current.time < .3 - 1.e-8 else ()
                return control.mix(dt, current, manual=manual)

            for _ in range(render_hz * 2):
                sim.step(1. / render_hz, {}, control_filter=per_tick)
            outcomes.append(tuple(sim.q) + tuple(sim.omega) + (sim.x, sim.y, sim.z))
        for other in outcomes[1:]:
            for actual, expected in zip(other, outcomes[0]):
                self.assertAlmostEqual(actual, expected, places=9)

    def test_60_and_120_hz_taps_have_comparable_feel(self):
        outcomes = []
        for hz in (60, 120):
            sim, control = airborne(hz), b.RotationControl()
            held = phase(sim, control, .1, ('RIGHT',))
            released = phase(sim, control, 1.2)
            outcomes.append((held['angle'][2] + released['angle'][2],
                             held['peak'][2]))
        self.assertLess(abs(outcomes[0][0] - outcomes[1][0]), math.radians(1.5))
        self.assertLess(abs(outcomes[0][1] - outcomes[1][1]), math.radians(3.))


if __name__ == '__main__':
    unittest.main()
