"""Air tilt/flick behavior through real simulated nozzle forces.

These host trajectories are tuning evidence, not badge sensor or timing proof.
The sensor owns pulse recognition/decay; this module tests the resulting signed
polls and the rotation controller's fixed-tick response.
"""
import importlib.util
import math
import sys
import unittest

from tools.tests.test_roadster_rotation_control import APP, airborne, rates, b, p


SPEC = importlib.util.spec_from_file_location('air_controls_tricks', APP / 'tricks.py')
t = importlib.util.module_from_spec(SPEC)
_previous = sys.modules.get('physics')
sys.modules['physics'] = p
try:
    SPEC.loader.exec_module(t)
finally:
    if _previous is None:
        del sys.modules['physics']
    else:
        sys.modules['physics'] = _previous


def model(tilt=(0., 0.), flick=(0., 0.), **kwargs):
    result = {'calibrated': True, 'armed': True, 'mode': 'both',
              'tilt_x': tilt[0], 'tilt_y': tilt[1],
              'flick_x': flick[0], 'flick_y': flick[1]}
    result.update(kwargs)
    return result


def phase(sim, controller, seconds, signal=b.NO_MOTION, buttons=(), manual=(), fan=0.):
    peak, angle, delivered = [0.] * 3, [0.] * 3, [0.] * 10
    for _ in range(round(seconds*sim.step_hz)):
        before = rates(sim)
        controls = controller.mix(sim.fixed_dt, sim, buttons=buttons, manual=manual,
                                  fan=fan, motion_signal=signal)
        sim.step(sim.fixed_dt, controls)
        after = rates(sim)
        for axis in range(3):
            peak[axis] = max(peak[axis], abs(after[axis]))
            angle[axis] += .5*(before[axis]+after[axis])*sim.fixed_dt
        for nozzle in range(10):
            delivered[nozzle] = max(delivered[nozzle], sim.nozzle_forces[nozzle])
    return {'peak': peak, 'angle': angle, 'force': delivered}


def pulse(sim, controller, axis, direction=1.):
    for tick in range(math.ceil(.22*sim.step_hz)):
        value = max(0., 1.-tick*sim.fixed_dt/.22)*direction
        signal = [0.] * 4
        signal[axis+2] = value
        phase(sim, controller, sim.fixed_dt, tuple(signal))


class AirControlTests(unittest.TestCase):
    def test_poll_preparation_gates_modes_calibration_and_is_an_immutable_copy(self):
        sample = model((.3, -.6), (-.8, .9))
        self.assertEqual(b.prepare_motion(sample), (.3, -.6, -.8, .9))
        self.assertEqual(b.prepare_motion(sample, enabled=False), b.NO_MOTION)
        for field in ('armed', 'calibrated', 'tilt_valid'):
            self.assertEqual(b.prepare_motion(dict(sample, **{field: False})), b.NO_MOTION)
        self.assertEqual(b.prepare_motion(dict(sample, sensor_clipped=True)), b.NO_MOTION)
        self.assertEqual(b.prepare_motion(dict(sample, mode='tilt')), (.3, -.6, 0., 0.))
        self.assertEqual(b.prepare_motion(dict(sample, mode='flick')), (0., 0., -.8, .9))
        prepared = b.prepare_motion(sample)
        sample['tilt_x'] = 1.
        self.assertEqual(prepared, (.3, -.6, -.8, .9))
        invalid = model((float('inf'), -20.), (None, float('nan')))
        self.assertEqual(b.prepare_motion(invalid), (0., -1., 0., 0.))

    def test_pending_vertical_stroke_delays_only_flick_not_continuous_tilt(self):
        pending = model((.4, -1.), flick_pending=True)
        self.assertEqual(b.prepare_motion(pending), (.4, -1., 0., 0.))
        # Both and Tilt remain continuous while the gesture recognizer waits.
        pending['mode'] = 'tilt'
        self.assertEqual(b.prepare_motion(pending), (.4, -1., 0., 0.))
        pending['mode'] = 'flick'
        self.assertEqual(b.prepare_motion(pending), b.NO_MOTION)

    def test_all_four_tilt_directions_work_without_thrust_buttons(self):
        for axis, body_axis, body_sign in ((0, 2, 1.), (1, 0, -1.)):
            for direction in (-1., 1.):
                with self.subTest(axis=axis, direction=direction):
                    sim, controller = airborne(), b.RotationControl()
                    tilt = [0., 0.]
                    tilt[axis] = direction
                    signal = b.prepare_motion(model(tilt))
                    result = phase(sim, controller, .5, signal)
                    self.assertGreater(rates(sim)[body_axis]*body_sign*direction, 1.5)
                    self.assertGreater(sum(result['force']), 0.)
                    self.assertTrue(controller.needs_feedback(signal))
                    self.assertFalse(any(controller.active))

    def test_twelve_second_holds_stay_near_requested_body_rate(self):
        for axis, body_axis, sign in ((0, 2, 1.), (1, 0, -1.)):
            for direction in (-1., 1.):
                with self.subTest(axis=axis, direction=direction):
                    sim, controller = airborne(), b.RotationControl()
                    signal = [0.] * 4
                    signal[axis] = direction
                    result = phase(sim, controller, 12., signal)
                    target = controller.TILT_RATES[axis]
                    self.assertGreater(rates(sim)[body_axis]*sign*direction, target*.97)
                    self.assertLess(result['peak'][body_axis], target*1.03)
                    self.assertTrue(sim.airborne)
                    self.assertTrue(all(math.isfinite(v) for v in sim.q+tuple(sim.omega)))

    def test_gentle_tilt_has_lower_rate_and_small_initial_correction(self):
        sim, controller = airborne(), b.RotationControl()
        result = phase(sim, controller, .2, (.2, 0., 0., 0.))
        self.assertLess(result['angle'][2], math.radians(4.))
        phase(sim, controller, 1.5, (.2, 0., 0., 0.))
        self.assertAlmostEqual(rates(sim)[2], math.radians(30.), delta=math.radians(1.))

    def test_neutral_coasts_without_counterthrust_or_leveling(self):
        sim, controller = airborne(), b.RotationControl()
        phase(sim, controller, .7, (1., 0., 0., 0.))
        before_rate, before_q = rates(sim)[2], sim.q
        result = phase(sim, controller, .5)
        self.assertEqual(result['force'], [0.] * 10)
        self.assertGreater(rates(sim)[2], before_rate*.95)
        self.assertNotEqual(sim.q, before_q)
        self.assertFalse(controller.needs_feedback())
        self.assertFalse(any(controller.motion_active))

    def test_opposite_tilt_brakes_then_reverses_with_opposite_lift_pair(self):
        sim, controller = airborne(), b.RotationControl()
        phase(sim, controller, 1., (1., 0., 0., 0.))
        before = rates(sim)[2]
        braking = phase(sim, controller, .3, (-1., 0., 0., 0.))
        self.assertGreater(rates(sim)[2], 0.)
        self.assertLess(rates(sim)[2], before*.55)
        self.assertGreater(braking['force'][5]+braking['force'][7],
                           braking['force'][4]+braking['force'][6])
        phase(sim, controller, .8, (-1., 0., 0., 0.))
        self.assertLess(rates(sim)[2], -math.radians(90.))

    def test_full_flick_makes_a_stronger_short_burst_then_coasts(self):
        for axis, body_axis, sign in ((0, 2, 1.), (1, 0, -1.)):
            with self.subTest(axis=axis):
                sim, controller = airborne(), b.RotationControl()
                pulse(sim, controller, axis)
                after = rates(sim)[body_axis]*sign
                self.assertGreater(after, math.radians(100.))
                self.assertLess(after, math.radians(135.))
                neutral = phase(sim, controller, .3)
                self.assertEqual(neutral['force'], [0.] * 10)
                self.assertGreater(rates(sim)[body_axis]*sign, after*.95)
                gentle, other = airborne(), b.RotationControl()
                signal = [0.] * 4
                signal[axis] = 1.
                phase(gentle, other, 14/60, signal)
                self.assertGreater(after, abs(rates(gentle)[body_axis])*1.7)

    def test_lift_flick_coast_and_countertilt_can_complete_an_airborne_flip(self):
        for axis, body_axis, sign in ((0, 2, 1.), (1, 0, -1.)):
            with self.subTest(axis=axis):
                sim, controller = p.Simulation('flat'), b.RotationControl()
                sim.set_step_hz(60)
                sim.set_air_mode('unlimited')
                phase(sim, controller, 1., buttons=('BACK',))
                self.assertTrue(sim.airborne)
                angle = 0.
                signal = [0.] * 4
                for tick in range(14):
                    signal[axis+2] = max(0., 1.-tick*sim.fixed_dt/.22)
                    result = phase(sim, controller, sim.fixed_dt, signal)
                    angle += result['angle'][body_axis]*sign
                # A pilot waits until near the finish before applying opposite
                # tilt. This estimates braking distance from measured rotation,
                # without giving the production controller an attitude target.
                # Re-evaluate stopping distance as aerodynamic damping slows
                # the coast; a fixed estimate from launch would brake early.
                while (angle < math.tau-rates(sim)[body_axis]**2/(2*controller.TILT_ACCEL)
                       and sim.time < 6.):
                    result = phase(sim, controller, sim.fixed_dt)
                    angle += result['angle'][body_axis]*sign
                    self.assertTrue(sim.airborne)
                signal = [0.] * 4
                signal[axis] = -1.
                while rates(sim)[body_axis]*sign > .02 and sim.time < 6.:
                    result = phase(sim, controller, sim.fixed_dt, signal)
                    angle += result['angle'][body_axis]*sign
                    self.assertTrue(sim.airborne)
                self.assertAlmostEqual(angle, math.tau, delta=math.radians(7.))
                self.assertGreater(sim.basis()[1][1], .99)
                self.assertLess(abs(rates(sim)[body_axis]), math.radians(3.))
                self.assertEqual(controller.mix(sim.fixed_dt, sim,
                                 motion_signal=b.NO_MOTION)['nozzles'], [0.] * 10)

    def test_repeated_flicks_and_diagonals_do_not_accumulate_runaway_rates(self):
        for signal in ((0., 0., 1., 0.), (0., 0., 0., 1.),
                       (0., 0., 1., 1.), (0., 0., -1., -1.),
                       (1., 1., 0., 0.)):
            with self.subTest(signal=signal):
                sim, controller = airborne(), b.RotationControl()
                result = phase(sim, controller, 12., signal)
                self.assertLess(max(result['peak']), math.radians(245.))
                self.assertTrue(all(math.isfinite(v) for v in sim.q+tuple(sim.omega)))
                self.assertTrue(sim.airborne)

    def test_flick_guard_preserves_opposite_counterthrust_and_never_clamps_velocity(self):
        sim, controller = airborne(), b.RotationControl()
        sim.omega = [0., 0., math.radians(300.)]
        before = tuple(sim.omega)
        same = controller.mix(sim.fixed_dt, sim, motion_signal=(0., 0., 1., 0.))
        self.assertEqual(same['nozzles'], [0.] * 10)
        opposite = controller.mix(sim.fixed_dt, sim, motion_signal=(0., 0., -1., 0.))
        self.assertGreater(opposite['nozzles'][5]+opposite['nozzles'][7], 0.)
        self.assertEqual(tuple(sim.omega), before)

    def test_motion_takes_clicky_axis_then_neutral_has_no_old_release_braking(self):
        sim, controller = airborne(), b.RotationControl()
        phase(sim, controller, .3, manual=('RIGHT',))
        self.assertTrue(controller.active[0])
        motion = controller.mix(sim.fixed_dt, sim, manual=('RIGHT',),
                                motion_signal=(-1., 0., 0., 0.))
        self.assertGreater(motion['nozzles'][5]+motion['nozzles'][7],
                           motion['nozzles'][4]+motion['nozzles'][6])
        self.assertFalse(controller.active[0])
        self.assertEqual(controller.mix(sim.fixed_dt, sim,
                         motion_signal=b.NO_MOTION)['nozzles'], [0.] * 10)

    def test_lift_boost_and_fan_work_during_tilt(self):
        sim, controller = airborne(), b.RotationControl()
        result = phase(sim, controller, .4, (.6, .4, 0., 0.),
                       buttons=('SELECT', 'BACK'), fan=1.)
        self.assertGreater(sim.vz, 2.)
        self.assertGreater(sim.vy, 2.)
        self.assertGreater(sum(result['force'][:4]), 15000.)
        self.assertGreater(sum(result['force'][4:8]), 50000.)
        self.assertGreater(sim.fan_speed, 0.)

    def test_no_air_means_no_delivered_rotation_or_magical_braking(self):
        sim, controller = airborne(), b.RotationControl()
        sim.set_air_mode('limited')
        sim.air_mass = p.TANK_RESIDUAL
        sim.mass = p.DRY_MASS+sim.air_mass
        sim.omega = [0., 0., 1.]
        before = rates(sim)[2]
        result = phase(sim, controller, .3, (-1., 0., -1., 0.))
        self.assertEqual(result['force'], [0.] * 10)
        self.assertGreater(rates(sim)[2], before*.95)

    def test_disarm_clears_motion_force_without_changing_pose_velocity_or_fuel(self):
        sim, controller = airborne(), b.RotationControl()
        phase(sim, controller, .3, (1., 0., 0., 0.))
        before = (sim.x, sim.y, sim.z, sim.vx, sim.vy, sim.vz,
                  sim.q, tuple(sim.omega), sim.air_mass, sim.time)
        blocked = b.prepare_motion(model((1., 1.), (1., 1.)), enabled=False)
        controls = controller.mix(sim.fixed_dt, sim, motion_signal=blocked)
        self.assertEqual(controls['nozzles'], [0.] * 10)
        self.assertEqual(before, (sim.x, sim.y, sim.z, sim.vx, sim.vy, sim.vz,
                                 sim.q, tuple(sim.omega), sim.air_mass, sim.time))
        self.assertFalse(controller.needs_feedback(blocked))

    def test_preset_can_own_forces_when_app_disables_prepared_motion(self):
        outcomes = []
        for model_input in (model(), model((1., 1.), (1., 1.))):
            sim, rotation, preset = p.Simulation('flat'), b.RotationControl(), t.TrickController()
            sim.set_step_hz(60)
            sim.set_air_mode('unlimited')
            self.assertTrue(preset.start('kickflip', sim))
            signal = b.prepare_motion(model_input, enabled=False)
            for _ in range(240):
                controls = rotation.mix(sim.fixed_dt, sim, motion_signal=signal)
                sim.step(sim.fixed_dt, controls, preset.update)
            outcomes.append((sim.x, sim.y, sim.z, sim.q, tuple(sim.omega), preset.active))
            self.assertFalse(preset.active)
            self.assertGreater(sim.basis()[1][1], .99)
        self.assertEqual(outcomes[0], outcomes[1])

    def test_prepared_feedback_is_independent_of_render_cadence(self):
        outcomes = []
        for fps in (15, 30, 60):
            sim, controller = airborne(), b.RotationControl()

            def per_tick(dt, current, unused):
                signal = (1., 0., 0., 0.) if current.time < .75-1.e-8 else b.NO_MOTION
                return controller.mix(dt, current, motion_signal=signal)

            for _ in range(fps*2):
                sim.step(1./fps, {}, per_tick)
            outcomes.append((sim.q, tuple(sim.omega), sim.y))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0], outcomes[2])


if __name__ == '__main__':
    unittest.main()
