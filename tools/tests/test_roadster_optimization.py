"""Conservative physics fast paths versus their full analytic queries.

Host equivalence checks only; native runtime and badge timing are separate.
"""
import importlib.util
import math
from pathlib import Path
import random
import unittest
def read_physics_field(sim, field):
    """Resolve lazy diagnostics through the public physics accessor."""
    return sim.get_fan_gap() if field == 'fan_gap' else getattr(sim, field)


SPEC = importlib.util.spec_from_file_location(
    'optimized_physics', Path(__file__).resolve().parents[2] /
    'apps/roadster_sandbox/physics.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def vehicle(x=0., z=0., yaw=0., length=4., width=2., height=2.):
    return {'x': x, 'z': z, 'yaw': yaw, 'length': length,
            'width': width, 'height': height}


def pose(rng):
    return p.q_axis(tuple(rng.uniform(-1., 1.) for _ in range(3)),
                    rng.uniform(-math.pi, math.pi))


class OptimizationTests(unittest.TestCase):
    def test_control_normalization_preserves_edge_cases_and_signed_zero(self):
        values = (None, True, False, '1', (), [], {}, float('nan'), float('inf'),
                  -float('inf'), -1.e13, -1.e12, -1., -0.5, -0., 0., .5, 1., 1.e12, 1.e13)
        rng = random.Random(807)
        values += tuple(rng.uniform(-2., 2.) for _ in range(100))
        for lo, hi in ((-1., 1.), (0., 1.)):
            for value in values:
                expected = p.clamp(p.number(value), lo, hi)
                actual = p._bounded_control(value, lo, hi)
                self.assertEqual(actual, expected, repr(value))
                self.assertIs(type(actual), type(expected))
                if actual == 0.:
                    self.assertEqual(math.copysign(1., actual), math.copysign(1., expected))

    def test_probe_selection_retains_every_possible_analytic_hit(self):
        rng = random.Random(802)
        sim = p.Simulation('flat', tuple(vehicle(
            rng.uniform(-10., 10.), rng.uniform(-10., 10.),
            rng.uniform(-math.pi, math.pi), rng.uniform(.2, 12.),
            rng.uniform(.2, 3.), rng.uniform(.2, 5.)) for _ in range(16)))
        for _ in range(400):
            pos = (rng.uniform(-15., 15.), rng.uniform(-1., 8.),
                   rng.uniform(-15., 15.))
            q = pose(rng)
            down = p.q_rotate(q, (0., -1., 0.))
            nearby = sim._probe_obstacles(pos)
            for point, distance in tuple((v, p.FAN_CAPTURE_HEIGHT) for v in p.SKIRT_PROBES) + tuple(
                    (v, p.WHEEL_REACH) for v in p.WHEELS):
                origin = p.add(pos, p.q_rotate(q, point))
                self.assertEqual(p.raycast(origin, down, distance, 'flat', nearby),
                                 p.raycast(origin, down, distance, 'flat', sim.obstacles))

    def test_parked_bounds_match_full_box_sweeps(self):
        rng = random.Random(803)
        cars = tuple(vehicle(rng.uniform(-5., 5.), rng.uniform(-5., 5.),
                             rng.uniform(-math.pi, math.pi),
                             rng.uniform(.1, 14.), rng.uniform(.1, 3.),
                             rng.uniform(.1, 5.)) for _ in range(10))
        fast, full = p.Simulation('flat', cars), p.Simulation('flat', cars)
        # Enormous horizontal bounds force the exact per-sample box tests for
        # every box; actual local geometry, normals and swept entry are intact.
        full.obstacles = tuple(box[:9] + (1.e6, 1.e6) for box in full.obstacles)
        for _ in range(180):
            fast.q = full.q = pose(rng)
            fast.x = full.x = rng.uniform(-18., 18.)
            fast.y = full.y = rng.uniform(-1., 7.)
            fast.z = full.z = rng.uniform(-18., 18.)
            old_pos = (rng.uniform(-18., 18.), rng.uniform(-1., 7.),
                       rng.uniform(-18., 18.))
            old_q = pose(rng)
            self.assertEqual(fast._parked_contacts(old_pos, old_q),
                             full._parked_contacts(old_pos, old_q))

    def test_airborne_probe_shortcuts_match_full_rays_and_residual_fan(self):
        rng = random.Random(804)
        distant = p.Simulation('flat', (vehicle(1000., 1000.),)).obstacles
        for _ in range(200):
            fast, full = p.Simulation('flat'), p.Simulation('flat')
            fast.q = full.q = pose(rng)
            fast.y = full.y = rng.uniform(2.600001, 50.)
            fast.vx = full.vx = rng.uniform(-5., 5.)
            fast.vy = full.vy = rng.uniform(-5., 5.)
            fast.vz = full.vz = rng.uniform(-5., 5.)
            fast.fan_pressure = full.fan_pressure = rng.uniform(0., 20000.)
            fast.fan_speed = full.fan_speed = rng.random()
            fan, steer = rng.random(), rng.uniform(-1., 1.)
            for sim, obstacles in ((fast, ()), (full, distant)):
                _, up, forward = sim.basis()
                pos = (sim.x, sim.y, sim.z)
                sim._fan(fan, p.scale(up, -1.), pos, obstacles)
                sim._wheels(.8, .3, steer, up, forward, pos, obstacles)
            for attr in ('_f', '_t', 'fan_gap', 'fan_force', 'fan_pressure',
                         'fan_speed', 'speed', 'steering', 'wheel_spin',
                         'wheel_compression', 'wheel_loads', 'wheel_contacts',
                         'contact_count'):
                self.assertEqual(read_physics_field(fast, attr),
                                 read_physics_field(full, attr), attr)

    def test_basis_cache_updates_after_direct_pose_assignment_step_and_reset(self):
        sim = p.Simulation('flat')
        first = sim.basis()
        self.assertIs(sim.basis(), first)
        sim.q = p.q_axis((1., 2., 3.), .7)
        for _ in range(3):
            expected = tuple(p.q_rotate(sim.q, axis) for axis in
                             ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)))
            self.assertEqual(sim.basis(), expected)
            self.assertIs(sim.basis(), sim.basis())
            sim.step(1. / 60, {'nozzles': [0.] * 4 + [1.]})
        sim.reset()
        self.assertEqual(sim.basis(), first)

    def test_grouped_thruster_directions_match_individual_rotations_exactly(self):
        rng = random.Random(805)
        for trial in range(400):
            fast, full = p.Simulation('flat'), p.Simulation('flat')
            fast.q = full.q = pose(rng)
            fast.air_mode = full.air_mode = 'limited' if trial % 2 else 'unlimited'
            fast.air_mass = full.air_mass = rng.uniform(p.TANK_RESIDUAL, p.TANK_MASS)
            fast.mass = full.mass = p.DRY_MASS + fast.air_mass
            fast._f = [rng.uniform(-30000., 30000.) for _ in range(3)]
            full._f = list(fast._f)
            fast._t = [rng.uniform(-3000., 3000.) for _ in range(3)]
            full._t = list(fast._t)
            # Cover full boost+lift, one isolated nozzle and arbitrary mixtures.
            duties = ([1.] * 10 if trial % 3 == 0 else
                      [float(i == trial % 10) for i in range(10)] if trial % 3 == 1 else
                      [rng.random() for _ in range(10)])
            pressure = p.tank_pressure(full.air_mass)
            forces = [0.] * 10
            supplied = p.TANK_MASS if full.air_mode == 'unlimited' else full.air_mass
            consumed = p.meter_nozzles(duties, supplied, full.fixed_dt, forces)
            for i, layout in enumerate(p.NOZZLES):
                force = forces[i]
                full.nozzle_forces[i] = force
                if force > 0.:
                    full._force(p.scale(p.q_rotate(full.q, layout[2]), force),
                                p.q_rotate(full.q, layout[1]))
            fast._thrusters(duties)
            self.assertEqual(fast._f, full._f)
            self.assertEqual(fast._t, full._t)
            self.assertEqual(fast.nozzle_forces, full.nozzle_forces)
            self.assertEqual(fast.pressure, pressure)
            expected_air = full.air_mass - (consumed
                                            if full.air_mode == 'limited' else 0.)
            self.assertEqual(fast.air_mass, expected_air)

    def test_inlined_contact_math_matches_full_vector_operations_exactly(self):
        rng = random.Random(806)
        for _ in range(1000):
            sim = p.Simulation('flat')
            sim.q = pose(rng)
            sim.mass = rng.uniform(p.DRY_MASS, p.DRY_MASS+p.TANK_MASS)
            vector = tuple(rng.uniform(-50000., 50000.) for _ in range(3))
            radius = tuple(rng.uniform(-2.5, 2.5) for _ in range(3))
            normal = p.unit(tuple(rng.uniform(-1., 1.) for _ in range(3)))
            def inverse(value):
                body = p.q_inverse_rotate(sim.q, value)
                factor = p.REFERENCE_MASS/sim.mass
                return p.q_rotate(sim.q, tuple(body[i]/p.INERTIA[i]*factor
                                              for i in range(3)))
            self.assertEqual(sim._inv_inertia(vector), inverse(vector))
            expected_mass = 1./sim.mass + p.dot(
                normal, p.cross(inverse(p.cross(radius, normal)), radius))
            self.assertEqual(sim._effective_inverse_mass(radius, normal), expected_mass)
            sim.vx, sim.vy, sim.vz = (rng.uniform(-30., 30.) for _ in range(3))
            sim.omega = [rng.uniform(-10., 10.) for _ in range(3)]
            expected_velocity = tuple(value+vector[i]/sim.mass for i, value in
                                      enumerate((sim.vx, sim.vy, sim.vz)))
            delta = inverse(p.cross(radius, vector))
            expected_omega = [sim.omega[i]+delta[i] for i in range(3)]
            sim._impulse(vector, radius)
            self.assertEqual((sim.vx, sim.vy, sim.vz), expected_velocity)
            self.assertEqual(sim.omega, expected_omega)


if __name__ == '__main__':
    unittest.main()
