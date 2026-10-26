"""Physical boundaries of the locally designed sampled-area suction fan."""
import math
import random
import unittest

import physics as p


class FanSealTests(unittest.TestCase):
    def sample(self, clearance):
        sim = p.Simulation('flat')
        pos = (0., clearance-p.FAN_PLANE_Y, 0.)
        return sim._fan_surface((0., -1., 0.), pos, (), 'flat', sim.q, True)

    def test_seal_is_bounded_monotone_and_smooth_at_both_ends(self):
        heights = [p.FAN_SEAL_HEIGHT+(p.FAN_CAPTURE_HEIGHT-p.FAN_SEAL_HEIGHT)*i/100.
                   for i in range(101)]
        area = [self.sample(height)[3]/len(p.SKIRT_PROBES) for height in heights]
        self.assertAlmostEqual(area[0], 1.)
        self.assertEqual(area[-1], 0.)
        self.assertTrue(all(0. <= value <= 1. for value in area))
        self.assertTrue(all(a >= b for a, b in zip(area, area[1:])))
        self.assertLess(abs(area[1]-area[0]), .001)
        self.assertLess(abs(area[-1]-area[-2]), .001)
        self.assertEqual(self.sample(p.FAN_CAPTURE_HEIGHT+1.)[3], 0.)

    def test_partial_platform_reduces_area_and_moves_force_center(self):
        platform = {'x': .64, 'z': 0., 'width': .8, 'length': 4., 'height': 2.,
                    'collision_boxes': ((0., 1., 0., .4, 1., 2.),)}
        sim = p.Simulation('flat', (platform,))
        pos = (0., 2.+p.FAN_SEAL_HEIGHT-p.FAN_PLANE_Y, 0.)
        _, cx, cz, area = sim._fan_surface((0., -1., 0.), pos, sim.obstacles, 'flat', sim.q, True)
        self.assertGreater(area, 0.)
        self.assertLess(area, len(p.SKIRT_PROBES))
        self.assertGreater(cx/area, .5)
        self.assertAlmostEqual(cz/area, 0.)
        for _ in range(240):
            sim._f[:] = [0., 0., 0.]
            sim._t[:] = [0., 0., 0.]
            sim._fan(1., (0., -1., 0.), pos)
        self.assertLess(sim._f[1], 0.)
        self.assertLess(sim._t[2], 0.)
        self.assertAlmostEqual(sim.fan_force/p.FAN_MAX_FORCE, area/len(p.SKIRT_PROBES), delta=.002)

    def test_flat_specialization_agrees_with_full_rays_for_tilted_poses(self):
        rng = random.Random(6626)
        far = p.Simulation('flat', ({'x': 1000., 'z': 1000., 'width': 1., 'length': 1., 'height': 1.},)).obstacles
        sim = p.Simulation('flat')
        for _ in range(150):
            q = p.q_axis((rng.random(), 0., rng.random()), rng.uniform(-math.pi, math.pi))
            down = p.q_rotate(q, (0., -1., 0.))
            pos = (0., rng.uniform(.2, 2.), 0.)
            fast = sim._fan_surface(down, pos, (), 'flat', q, True)
            full = sim._fan_surface(down, pos, far, 'flat', q, True)
            for a, b in zip(fast, full):
                self.assertAlmostEqual(a, b, places=10)


if __name__ == '__main__':
    unittest.main()
