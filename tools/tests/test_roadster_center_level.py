"""Return-to-center recovery measured through the real portable physics.

These are host checks, not claims about physical-badge feel or performance.
The fixtures start aloft; every subsequent change of attitude comes from jets.
"""
import importlib.util
import math
from pathlib import Path
import sys
import unittest


APP = Path(__file__).resolve().parents[2] / 'apps' / 'roadster_sandbox'
SPEC = importlib.util.spec_from_file_location('center_level_physics', APP / 'physics.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
SPEC = importlib.util.spec_from_file_location('center_level_controls', APP / 'badge_controls.py')
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


def airborne(axis=(0., 0., 1.), angle=0., body_spin=(0., 0., 0.), hz=60):
    sim = p.Simulation('flat')
    sim.set_step_hz(hz)
    sim.set_air_mode('unlimited')
    sim.y = 1000.
    sim.q = p.q_axis(axis, angle)
    sim.omega = list(p.q_rotate(sim.q, body_spin))
    sim.airborne = True
    return sim


def phase(sim, controller, seconds, signal=b.NO_MOTION, **kwargs):
    roll = pitch = 0.
    delivered = [0.] * 10
    for _ in range(round(seconds*sim.step_hz)):
        before = p.q_inverse_rotate(sim.q, sim.omega)
        controls = controller.mix(sim.fixed_dt, sim, motion_signal=signal,
                                  level_enabled=True, **kwargs)
        sim.step(sim.fixed_dt, controls)
        after = p.q_inverse_rotate(sim.q, sim.omega)
        pitch += (before[0]+after[0])*.5*sim.fixed_dt
        roll += (before[2]+after[2])*.5*sim.fixed_dt
        delivered = [max(a, c) for a, c in zip(delivered, sim.nozzle_forces)]
    return roll, pitch, delivered


class CenterLevelTests(unittest.TestCase):
    def assert_upright(self, sim, angle=2., rate=.07):
        self.assertGreater(sim.basis()[1][1], math.cos(math.radians(angle)))
        body_rate = p.q_inverse_rotate(sim.q, sim.omega)
        self.assertLess(abs(body_rate[0]), rate)
        self.assertLess(abs(body_rate[2]), rate)

    def test_recovers_all_directions_vertical_inverted_and_combined_tumbles(self):
        cases = (
            ((0., 0., 1.), 1.5, (0., 0., 0.)),
            ((0., 0., 1.), -1.5, (0., 0., 0.)),
            ((1., 0., 0.), 1.5, (0., 0., 0.)),
            ((1., 0., 0.), -1.5, (0., 0., 0.)),
            ((1., 0., 0.), math.pi/2, (0., 0., 0.)),
            ((0., 0., 1.), math.pi, (0., 0., 0.)),
            ((1., 0., 0.), math.pi, (0., 0., 0.)),
            ((1., 0., 1.), math.pi, (0., 0., 0.)),
            ((1., 0., 1.), 2.7, (1., .3, 2.)),
            ((0., 0., 1.), 1.2, (0., 0., 3.7)),
        )
        for hz in (60, 120):
            for axis, angle, spin in cases:
                with self.subTest(hz=hz, axis=axis, angle=angle, spin=spin):
                    sim = airborne(axis, angle, spin, hz)
                    control = b.RotationControl()
                    _, _, forces = phase(sim, control, 3.)
                    self.assert_upright(sim)
                    self.assertGreater(sum(forces), 100.)
                    self.assertGreater(sim.y, 900.)

    def test_full_tilt_keeps_rotating_then_center_brakes_and_rights(self):
        for signal, index, sign in (((1., 0., 0., 0.), 0, 1.),
                                    ((-1., 0., 0., 0.), 0, -1.),
                                    ((0., 1., 0., 0.), 1, -1.),
                                    ((0., -1., 0., 0.), 1, 1.)):
            with self.subTest(signal=signal):
                sim, control = airborne(), b.RotationControl()
                result = phase(sim, control, 4., signal)
                self.assertGreater(result[index]*sign, math.tau)
                self.assertEqual(control.level_state, 'tilt')
                phase(sim, control, 3.)
                self.assert_upright(sim)
                phase(sim, control, .3, signal)
                self.assertEqual(control.level_state, 'tilt')
                rate = p.q_inverse_rotate(sim.q, sim.omega)
                self.assertGreater(rate[2 if index == 0 else 0]*sign, .6)

    def test_mixer_never_changes_pose_velocity_or_air(self):
        sim, control = airborne(angle=1.7, body_spin=(.8, .4, 2.)), b.RotationControl()
        before = (sim.q, tuple(sim.omega), sim.x, sim.y, sim.z,
                  sim.vx, sim.vy, sim.vz, sim.air_mass, sim.time)
        output = control.mix(sim.fixed_dt, sim, level_enabled=True)
        self.assertEqual(before, (sim.q, tuple(sim.omega), sim.x, sim.y, sim.z,
                                 sim.vx, sim.vy, sim.vz, sim.air_mass, sim.time))
        self.assertGreater(sum(output['nozzles']), 0.)

    def test_visible_small_lean_releases_leveling_and_keeps_rotating(self):
        # Six degrees is only 8% input after the sensor's 4-degree dead zone.
        # Once visible tilt requests rotation, previously latched leveling
        # must not counteract it or hold the car at a small bank angle.
        for degrees in (6., 8.):
            for axis, body_axis, body_sign in ((0, 2, 1.), (1, 0, -1.)):
                for sign in (-1., 1.):
                    with self.subTest(degrees=degrees, axis=axis, sign=sign):
                        sim, control = airborne(), b.RotationControl()
                        phase(sim, control, .2)
                        self.assertEqual(control.level_state, 'steady')
                        signal = [0.] * 4
                        signal[axis] = sign*(degrees-4.)/24.
                        result = phase(sim, control, 3., tuple(signal))
                        target = control.TILT_RATES[axis]*abs(signal[axis])
                        self.assertEqual(control.level_state, 'tilt')
                        self.assertEqual(control.level_strength, 0.)
                        body_rate = p.q_inverse_rotate(sim.q, sim.omega)
                        self.assertGreater(body_rate[body_axis]*body_sign*sign, target*.9)
                        self.assertGreater(result[axis]*body_sign*sign, target*2.)
                        self.assertGreater(sum(result[2]), 0.)

    def test_defaults_keep_free_tumbles_and_disabled_assist_stops_immediately(self):
        sim, control = airborne(angle=1., body_spin=(0., 0., 2.)), b.RotationControl()
        self.assertEqual(control.mix(sim.fixed_dt, sim)['nozzles'], [0.]*10)
        control.mix(sim.fixed_dt, sim, level_enabled=True)
        self.assertGreater(control.level_strength, 0.)
        self.assertEqual(control.mix(sim.fixed_dt, sim)['nozzles'], [0.]*10)
        self.assertEqual(control.level_state, 'off')
        self.assertFalse(control.needs_feedback())
        self.assertTrue(control.needs_feedback(level_enabled=True))

    def test_ground_roof_and_manual_clicky_input_suppress_leveling(self):
        for field in ('contact_count', 'chassis_contacts', 'airborne'):
            sim, control = airborne(angle=1.7), b.RotationControl()
            setattr(sim, field, False if field == 'airborne' else 1)
            output = control.mix(sim.fixed_dt, sim, level_enabled=True)
            self.assertEqual(output['nozzles'], [0.]*10)
            self.assertEqual(control.level_state, 'off')
        for manual in (('RIGHT',), ('UP',), ('LEFT', 'RIGHT')):
            sim = airborne(angle=1.7)
            ordinary, assisted = b.RotationControl(), b.RotationControl()
            self.assertEqual(ordinary.mix(sim.fixed_dt, sim, manual=manual),
                             assisted.mix(sim.fixed_dt, sim, manual=manual,
                                          level_enabled=True))
            self.assertEqual(assisted.level_state, 'off')

    def test_center_blend_hysteresis_and_flick_priority(self):
        sim, control = airborne(angle=1.), b.RotationControl()
        def poll(tilt, flick=0.):
            return control.mix(sim.fixed_dt, sim, level_enabled=True,
                               motion_signal=(tilt, 0., flick, 0.))
        poll(.01)
        self.assertEqual(control.level_strength, 0.)
        poll(0.)
        self.assertEqual(control.level_strength, 1.)
        poll(.01)
        self.assertGreater(control.level_strength, 0.)
        self.assertLess(control.level_strength, 1.)
        poll(.03)
        self.assertEqual(control.level_strength, 0.)
        poll(.01)
        self.assertEqual(control.level_strength, 0.)
        poll(0.)
        actual = poll(0., 1.)
        expected = b.RotationControl().mix(sim.fixed_dt, sim,
                                           motion_signal=(0., 0., 1., 0.))
        self.assertEqual(actual, expected)
        self.assertEqual(control.level_strength, 0.)

    def test_flick_only_mode_uses_measured_grip_instead_of_zero_torque_signal(self):
        sim, control = airborne(angle=1.), b.RotationControl()
        motion = {'mode': 'flick', 'calibrated': True, 'armed': True,
                  'tilt_x': .8, 'tilt_y': 0.}
        signal = b.prepare_motion(motion)
        self.assertEqual(signal, b.NO_MOTION)
        output = control.mix(sim.fixed_dt, sim, motion=motion,
                             motion_signal=signal, level_enabled=True)
        self.assertEqual(output['nozzles'], [0.]*10)
        self.assertEqual(control.level_state, 'tilt')
        motion['tilt_x'] = 0.
        control.mix(sim.fixed_dt, sim, motion=motion,
                    motion_signal=signal, level_enabled=True)
        self.assertEqual(control.level_state, 'level')

    def test_exhausted_tank_cannot_supply_recovery_torque(self):
        sim, control = airborne(angle=1.), b.RotationControl()
        sim.set_air_mode('limited')
        sim.air_mass = p.TANK_RESIDUAL
        sim.mass = p.DRY_MASS+sim.air_mass
        _, _, forces = phase(sim, control, 2.)
        self.assertLess(sum(forces), 1.e-6)
        self.assertLess(sim.basis()[1][1], .6)
        self.assertEqual(control.level_state, 'level')

    def test_lower_pressure_recovery_spends_air_and_has_less_authority(self):
        outcomes = []
        # At a quarter tank the regulator still supplies full thrust. Use
        # the final 1% of usable gas to exercise genuinely low supply pressure.
        low_air = p.TANK_RESIDUAL+(p.TANK_MASS-p.TANK_RESIDUAL)*.01
        self.assertLess(p.supply_fraction(low_air), .25)
        for initial_air in (p.TANK_MASS, low_air):
            sim, control = airborne((1., 0., 1.), 2.6), b.RotationControl()
            sim.set_air_mode('limited')
            sim.air_mass = initial_air
            sim.mass = p.DRY_MASS+initial_air
            phase(sim, control, 4.)
            self.assertLess(sim.air_mass, initial_air)
            self.assertGreaterEqual(sim.air_mass, p.TANK_RESIDUAL)
            self.assertTrue(all(math.isfinite(x) for x in sim.q+tuple(sim.omega)))
            outcomes.append(sim.basis()[1][1])
        self.assertGreater(outcomes[0], math.cos(math.radians(2.)))
        self.assertLess(outcomes[1], math.cos(math.radians(20.)))

    def test_recovery_never_applies_yaw_torque_or_targets_heading(self):
        sim, control = airborne((1., 0., 1.), 2.5), b.RotationControl()
        for _ in range(180):
            output = control.mix(sim.fixed_dt, sim, level_enabled=True)
            torque = [0., 0., 0.]
            for duty, nozzle in zip(output['nozzles'], p.NOZZLES):
                self.assertGreaterEqual(duty, 0.)
                self.assertLessEqual(duty, 1.)
                moment = p.cross(nozzle[1], p.scale(nozzle[2], duty*nozzle[3]))
                torque = p.add(torque, moment)
            self.assertAlmostEqual(torque[1], 0., places=7)
            sim.step(sim.fixed_dt, output)
        self.assert_upright(sim)
        # An upright yaw spin is a valid level orientation; it remains free.
        sim = airborne(body_spin=(0., 1., 0.))
        control = b.RotationControl()
        self.assertEqual(control.mix(sim.fixed_dt, sim, level_enabled=True)['nozzles'],
                         [0.]*10)
        self.assertEqual(control.level_state, 'steady')

    def test_boost_lift_and_fan_remain_independent_during_recovery(self):
        sim, control = airborne(angle=1.3), b.RotationControl()
        _, _, forces = phase(sim, control, 3., buttons=('SELECT', 'BACK'), fan=1.)
        self.assert_upright(sim, angle=4., rate=.1)
        self.assertGreater(sum(forces[:4]), 23000.)
        self.assertGreater(sum(forces[4:8]), 60000.)
        output = control.mix(sim.fixed_dt, sim, buttons=('SELECT', 'BACK'),
                             fan=1., level_enabled=True)
        self.assertEqual(output['fan'], 1.)

    def test_fixed_tick_recovery_does_not_depend_on_render_cadence(self):
        outcomes = []
        for render_hz in (15, 30, 60):
            sim, control = airborne((1., 0., 1.), 2.5, (1., .2, 2.)), b.RotationControl()
            def tick(dt, current, unused):
                return control.mix(dt, current, level_enabled=True)
            for _ in range(render_hz*3):
                sim.step(1./render_hz, {}, control_filter=tick)
            outcomes.append(sim.q+tuple(sim.omega)+(sim.x, sim.y, sim.z))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0], outcomes[2])

    def test_reset_clears_recovery_and_restores_deterministic_inverted_choice(self):
        sim, control = airborne(angle=1.), b.RotationControl()
        control.mix(sim.fixed_dt, sim, level_enabled=True)
        control.reset()
        self.assertEqual(control.snapshot(), b.RotationControl().snapshot())


if __name__ == '__main__':
    unittest.main()
