"""Badge mixer behavior through the portable Roadster physics, without hardware."""
import importlib.util
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox'
SPEC = importlib.util.spec_from_file_location('badge_mixer_physics', APP / 'physics.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
SPEC = importlib.util.spec_from_file_location('roadster_badge_controls', APP / 'badge_controls.py')
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


def airborne_step(controls):
    sim = p.Simulation('flat')
    sim.y = 100.
    sim.step(p.FIXED_DT, controls)
    return sim


def nozzle_torque(controls):
    result = [0., 0., 0.]
    for duty, nozzle in zip(controls['nozzles'], p.NOZZLES):
        torque = p.cross(nozzle[1], p.scale(nozzle[2], duty*nozzle[3]))
        for axis in range(3):
            result[axis] += torque[axis]
    return result


class BadgeControlsTests(unittest.TestCase):
    def test_drive_brake_reverse_and_opposing_dpad(self):
        drive = b.mix_controls(('UP', 'LEFT'))
        self.assertEqual((drive['throttle'], drive['steer']), (1., 1.))
        self.assertEqual(b.mix_controls(('RIGHT',))['steer'], -1.)
        self.assertEqual(b.mix_controls(('LEFT', 'RIGHT'))['steer'], 0.)
        braking = b.mix_controls(('DOWN',), speed=8.)
        self.assertEqual((braking['throttle'], braking['brake']), (0., 1.))
        reverse = b.mix_controls(('DOWN',), speed=0.)
        self.assertEqual((reverse['throttle'], reverse['brake']), (-1., 0.))
        stopped = b.mix_controls(('UP', 'DOWN'))
        self.assertEqual((stopped['throttle'], stopped['brake']), (0., 1.))
        sim = p.Simulation('flat')
        for _ in range(60):
            sim.step(p.FIXED_DT, reverse)
        self.assertLess(sim.speed, -1.)

    def test_neutral_boost_lift_and_both_are_independent(self):
        boost = b.mix_controls(('SELECT',))
        self.assertEqual(boost['nozzles'], [1.]*4+[0.]*6)
        lifted = b.mix_controls(('BACK',))
        self.assertEqual(lifted['nozzles'], [0.]*4+[1.]*4+[0.]*2)
        combined = b.mix_controls(('SELECT', 'BACK'))
        self.assertEqual(combined['nozzles'], [1.]*8+[0.]*2)
        boosted = airborne_step(boost)
        rising = airborne_step(lifted)
        self.assertGreater(boosted.vz, 0.)
        self.assertGreater(rising.vy, 0.)
        self.assertLess(rising.air_mass, p.TANK_MASS)

    def test_motion_requires_held_thrust_and_ready_controller(self):
        sample = {'calibrated': True, 'armed': True,
                  'channels': {'right': 1., 'up': 1.}}
        self.assertEqual(b.mix_controls((), sample)['nozzles'], [0.]*10)
        for field in ('armed', 'calibrated'):
            invalid = dict(sample)
            invalid[field] = False
            self.assertEqual(b.mix_controls(('SELECT',), invalid)['nozzles'], [1.]*4+[0.]*6)
        self.assertTrue(any(b.mix_controls(('SELECT',), sample)['nozzles'][4:]))

    def test_clicky_directions_fire_without_touch_or_motion_calibration(self):
        stale_motion = {'calibrated': False, 'armed': False,
                        'channels': {'right': 1., 'up': 1.}}
        for direction, axis, sign in (('LEFT', 2, -1.), ('RIGHT', 2, 1.),
                                      ('UP', 0, -1.), ('DOWN', 0, 1.)):
            controls = b.mix_controls((), stale_motion, manual=(direction,))
            self.assertEqual(controls['throttle'], 0.)
            self.assertEqual(controls['steer'], 0.)
            # Require useful angular acceleration, independently of the
            # particular vehicle mass and nozzle mounting dimensions.
            self.assertGreater(nozzle_torque(controls)[axis] * sign / p.INERTIA[axis], 8.)
            self.assertGreater(airborne_step(controls).omega[axis] * sign, 0.)

    def test_clicky_axes_do_not_duplicate_touch_driving_or_boost(self):
        controls = b.mix_controls(('UP', 'LEFT', 'SELECT'), manual=('RIGHT', 'UP'))
        self.assertEqual(controls['throttle'], 1.)
        self.assertEqual(controls['steer'], 1.)
        self.assertGreater(sum(controls['nozzles'][:4]), 3.)
        torque = nozzle_torque(controls)
        self.assertGreater(torque[2] / p.INERTIA[2], 8.)
        self.assertLess(torque[0] / p.INERTIA[0], -8.)
        # The clicky center is an app-level fan edge, never a held boost.
        self.assertEqual(b.mix_controls(manual=('SELECT',))['nozzles'], [0.]*10)

    def test_clicky_and_motion_combine_before_one_bounded_jet_allocation(self):
        channels = {'calibrated': True, 'armed': True,
                    'channels': {'right': 1., 'up': .4}}
        neutral_roll = b.mix_controls(('BACK',), channels, manual=('LEFT',))
        self.assertAlmostEqual(nozzle_torque(neutral_roll)[2], 0.)
        self.assertLess(nozzle_torque(neutral_roll)[0], 0.)
        for manual in (('RIGHT', 'UP'), ('LEFT', 'DOWN'),
                       ('LEFT', 'RIGHT', 'UP', 'DOWN')):
            combined = b.mix_controls(('SELECT', 'BACK'), channels, manual=manual)
            self.assertTrue(all(0. <= value <= 1. for value in combined['nozzles']))
            self.assertAlmostEqual(nozzle_torque(combined)[1], 0., places=8)
        opposing = b.mix_controls(manual=('LEFT', 'RIGHT', 'UP', 'DOWN'))
        self.assertEqual(opposing['nozzles'], [0.]*10)

    def test_manual_fan_and_boost_still_share_physical_forces(self):
        controls = b.mix_controls(('SELECT', 'BACK'), fan=1., manual=('DOWN',))
        self.assertEqual(controls['fan'], 1.)
        self.assertEqual(controls['nozzles'][:4], [1.]*4)
        self.assertGreater(nozzle_torque(controls)[0] / p.INERTIA[0], 15.)
        self.assertGreater(sum(airborne_step(controls).nozzle_forces), 0.)

    def test_motion_roll_sign_side_assist_and_yaw_cancellation(self):
        for buttons in (('SELECT',), ('BACK',), ('SELECT', 'BACK')):
            for channel, sign, side in (('right', 1., 8), ('left', -1., 9)):
                controls = b.mix_controls(buttons, {channel: 1.})
                self.assertGreater(controls['nozzles'][side], 0.)
                torque = nozzle_torque(controls)
                self.assertGreater(torque[2]*sign / p.INERTIA[2], 15.)
                self.assertAlmostEqual(torque[1], 0., places=8)
                sim = airborne_step(controls)
                self.assertGreater(sim.omega[2]*sign, 0.)
                self.assertLess(abs(sim.omega[1]), .001)

    def test_pitch_uses_front_or_rear_lift_while_lift_is_held(self):
        for channel, sign in (('up', -1.), ('down', 1.)):
            for buttons in (('BACK',), ('SELECT',), ('SELECT', 'BACK')):
                controls = b.mix_controls(buttons, {channel: 1.})
                self.assertGreater(nozzle_torque(controls)[0]*sign / p.INERTIA[0], 15.)
                sim = airborne_step(controls)
                self.assertGreater(sim.omega[0]*sign, 0.)
                self.assertAlmostEqual(sim.omega[2], 0., places=8)

    def test_lift_does_not_mask_weaker_proportional_motion(self):
        full = b.mix_controls(('BACK',), {'right': 1.})
        quarter = b.mix_controls(('BACK',), {'right': .25})
        self.assertAlmostEqual(nozzle_torque(quarter)[2], nozzle_torque(full)[2]*.25)
        self.assertGreater(sum(quarter['nozzles'][4:8]), sum(full['nozzles'][4:8]))
        self.assertGreater(airborne_step(quarter).omega[2], 0.)

    def test_opposing_motion_cancels_and_diagonal_shares_authority(self):
        neutral = b.mix_controls(('BACK',), {'left': .8, 'right': .8, 'up': 1., 'down': 1.})
        self.assertEqual(neutral['nozzles'], [0.]*4+[1.]*4+[0.]*2)
        diagonal = b.mix_controls(('BACK',), {'right': 1., 'up': 1.})
        torque = nozzle_torque(diagonal)
        self.assertGreater(torque[2] / p.INERTIA[2], 8.)
        self.assertLess(torque[0] / p.INERTIA[0], -8.)
        self.assertTrue(all(0. <= duty <= 1. for duty in diagonal['nozzles']))

    def test_fan_and_thrusters_work_together_without_pose_changes(self):
        controls = b.mix_controls(('SELECT', 'BACK'), {'up': .5}, fan=1.)
        self.assertEqual(controls['fan'], 1.)
        self.assertGreater(sum(controls['nozzles']), 0.)
        sim = p.Simulation('flat')
        sim.step(p.FIXED_DT, controls)
        self.assertGreater(sum(sim.nozzle_forces), 0.)
        self.assertGreater(sim.fan_speed, 0.)
        self.assertEqual(sim.total_steps, 1)

    def test_invalid_motion_is_bounded_and_inputs_are_not_mutated(self):
        sample = {'right': 100., 'left': float('nan'), 'up': float('inf'), 'down': None}
        controls = b.mix_controls(('SELECT', 'BACK'), sample, fan=3.)
        self.assertTrue(all(0. <= duty <= 1. for duty in controls['nozzles']))
        self.assertEqual(controls['fan'], 1.)
        self.assertEqual(sample['right'], 100.)
        self.assertEqual(sample['down'], None)


if __name__ == '__main__':
    unittest.main()
