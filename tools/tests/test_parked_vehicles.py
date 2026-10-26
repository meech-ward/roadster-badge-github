"""Static fleet collision behavior, independent of rendering and badge I/O.

Native MicroPython and rendered placement checks are separate acceptance steps.
The collision shape is deliberately a compound-box approximation of each car.
"""
import importlib.util
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'parked_physics', ROOT / 'apps/roadster_sandbox/physics.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def parked(**values):
    result = {'id': 'semi', 'x': 0., 'z': 0., 'yaw': 0.,
              'collision_boxes': ((0., 1.25, 0., 1.6, 1.25, 3.5),)}
    result.update(values)
    return result


def advance(sim, seconds, controls=None, fps=120):
    for _ in range(round(seconds*fps)):
        sim.step(1./fps, controls)
    return sim


class ParkedVehicleTests(unittest.TestCase):
    def test_configuration_preserves_motion_and_reset_preserves_world(self):
        sim = advance(p.Simulation('flat'), .5, {'throttle': 1})
        before = (sim.x, sim.y, sim.z, sim.time, sim.vz, sim.air_mass)
        self.assertEqual(sim.set_obstacles((parked(),)), 1)
        self.assertEqual(before, (sim.x, sim.y, sim.z, sim.time, sim.vz, sim.air_mass))
        boxes = sim.obstacles
        sim.reset('ramp')
        self.assertEqual(sim.obstacles, boxes)
        self.assertEqual(sim.time, 0.)
        self.assertEqual(sim.scene_name, 'ramp')
        self.assertEqual(sim.set_obstacles(()), 0)

    def test_invalid_configuration_is_rejected_without_losing_previous_world(self):
        sim = p.Simulation('flat', (parked(),))
        boxes = sim.obstacles
        for shape in (((0, 1, 0, -1, 1, 1),),
                      ((0, 1, 0, 1, float('nan'), 1),),
                      ((0, 1),), ((0, 1, 0, 1, 1, 1),)*129):
            with self.subTest(shape=shape[:1]):
                with self.assertRaises(ValueError):
                    sim.set_obstacles((parked(collision_boxes=shape),))
                self.assertEqual(sim.obstacles, boxes)

    def test_roof_wheel_raycast_and_ground_agree_with_rotated_placement(self):
        sim = p.Simulation('flat', (parked(x=8, z=-3, yaw=math.pi/3),))
        hit = p.raycast((8, 6, -3), (0, -1, 0), 8, 'flat', sim.obstacles)
        self.assertAlmostEqual(hit[0], 3.5)
        self.assertEqual(hit[1], (0., 1., 0.))
        self.assertEqual(hit[2], 'vehicle:semi')
        ground = p.raycast((0, 6, -3), (0, -1, 0), 8, 'flat', sim.obstacles)
        self.assertEqual(ground, (6., (0., 1., 0.), 'ground'))

    def test_default_compound_body_does_not_fill_nose_to_roof_height(self):
        sim = p.Simulation('flat', ({'id': 'sedan', 'x': 0, 'z': 0,
                                    'length': 5., 'width': 2., 'height': 1.5},))
        roof = p.raycast((0, 4, 0), (0, -1, 0), 5, 'flat', sim.obstacles)
        nose = p.raycast((0, 4, 2.1), (0, -1, 0), 5, 'flat', sim.obstacles)
        self.assertAlmostEqual(4-roof[0], 1.5)
        self.assertAlmostEqual(4-nose[0], .84)

    def test_front_impact_uses_near_face_even_when_tick_crosses_thin_body(self):
        for thickness in (5., .05):
            with self.subTest(thickness=thickness):
                sim = p.Simulation('flat', (parked(collision_boxes=(
                    (0., 1.25, 0., 1.6, 1.25, thickness/2),)),))
                sim.z, sim.vz = -9., 100.
                for _ in range(30):
                    sim.step(p.FIXED_DT)
                    if sim.chassis_contacts:
                        break
                self.assertGreater(sim.chassis_contacts, 0)
                self.assertEqual(sim.surface, 'vehicle:semi')
                # At first impact every chassis sample remains on the approach
                # side, even for a 5 cm panel crossed within one 120 Hz tick.
                nose = max(sim.z+p.q_rotate(sim.q, point)[2] for point in p.HULL)
                self.assertLessEqual(nose, -thickness/2+.003)
                self.assertLess(sim.vz, 2.)
                self.assertGreater(sim.time, 0.)
                self.assertEqual(sim.air_mass, p.TANK_MASS)

    def test_high_speed_side_impact_does_not_pass_through(self):
        sim = p.Simulation('flat', (parked(),))
        sim.x, sim.z, sim.vx = -6., 0., 100.
        for _ in range(30):
            sim.step(p.FIXED_DT)
            if sim.chassis_contacts:
                break
        self.assertGreater(sim.chassis_contacts, 0)
        side = max(sim.x+p.q_rotate(sim.q, point)[0] for point in p.HULL)
        self.assertLessEqual(side, -1.6+.003)
        self.assertLess(sim.vx, 2.)

    def test_rotated_vehicle_stops_a_local_front_impact(self):
        angle = .7
        c, s = math.cos(angle), math.sin(angle)
        sim = p.Simulation('flat', (parked(x=5, z=4, yaw=angle),))
        sim.x, sim.z = 5-s*9, 4-c*9
        sim.q = p.q_axis((0, 1, 0), angle)
        sim.vx, sim.vz = s*60, c*60
        for _ in range(60):
            sim.step(p.FIXED_DT)
            if sim.chassis_contacts:
                break
        self.assertGreater(sim.chassis_contacts, 0)
        for point in p.HULL:
            world = p.add((sim.x, sim.y, sim.z), p.q_rotate(sim.q, point))
            local_z = s*(world[0]-5)+c*(world[2]-4)
            self.assertLessEqual(local_z, -3.5+.003)
        self.assertLess(s*sim.vx+c*sim.vz, 2.)

    def test_landing_on_roof_is_supported_then_lift_works_again(self):
        sim = p.Simulation('flat', (parked(),))
        sim.z, sim.y = 0., 6.
        advance(sim, 3.)
        self.assertEqual(sim.contact_count, 4)
        self.assertAlmostEqual(sim.y, 2.5+p.GROUND_Y, delta=.004)
        self.assertEqual(sim.surface, 'vehicle:semi')
        self.assertAlmostEqual(sim.vy, 0., delta=.01)
        self.assertGreater(sim.basis()[1][1], .99)
        self.assertEqual(sim.time, 3.)
        self.assertEqual(sim.air_mass, p.TANK_MASS)
        advance(sim, .5, {'nozzles': [0]*4+[1]*4})
        self.assertGreater(sim.y, 5.)
        self.assertGreater(sim.vy, 5.)
        self.assertTrue(sim.airborne)
        self.assertLess(sim.air_fraction, 1.)
        self.assertEqual(sim.time, 3.5)

    def test_overturned_landing_remains_overturned_and_can_slide(self):
        sim = p.Simulation('flat', (parked(),))
        sim.z, sim.y, sim.q = 0., 6., (0., 0., 0., 1.)
        advance(sim, 3.)
        self.assertLess(sim.basis()[1][1], -.99)
        self.assertAlmostEqual(sim.y, 2.5+(max(v[1] for v in p.HULL)+.001), delta=.015)
        self.assertEqual(sim.contact_count, 0)
        start = sim.z
        advance(sim, .5, {'nozzles': [1]*4})
        self.assertGreater(abs(sim.z-start), .2)
        self.assertEqual(sim.time, 3.5)
        self.assertLess(sim.air_mass, p.TANK_MASS)

    def test_clear_jump_over_roof_has_no_invisible_high_wall(self):
        sim = p.Simulation('flat', (parked(),))
        sim.z, sim.y, sim.vz = -10., 8., 25.
        for _ in range(96):
            sim.step(p.FIXED_DT)
            self.assertEqual(sim.chassis_contacts, 0)
            self.assertEqual(sim.contact_count, 0)
        self.assertGreater(sim.z, 9.)
        self.assertGreater(sim.y, 4.)
        self.assertEqual(sim.air_mass, p.TANK_MASS)

    def test_driving_alongside_vehicle_remains_unobstructed(self):
        sim = p.Simulation('flat', (parked(),))
        sim.x, sim.z = 3., -9.
        advance(sim, 2., {'throttle': 1})
        self.assertGreater(sim.z, 10.)
        self.assertAlmostEqual(sim.x, 3., delta=.01)
        self.assertEqual(sim.chassis_contacts, 0)
        self.assertEqual(sim.surface, 'ground')

    def test_fan_adds_roof_contact_load_without_spending_air(self):
        sim = p.Simulation('flat', (parked(),))
        sim.z, sim.y = 0., 2.5+p.GROUND_Y
        advance(sim, 1.)
        normal_load = sum(sim.wheel_loads)
        advance(sim, 1., {'fan': 1})
        self.assertEqual(sim.contact_count, 4)
        self.assertGreater(sum(sim.wheel_loads), normal_load*2)
        self.assertGreater(sim.fan_force, p.FAN_MAX_FORCE*.85)
        self.assertEqual(sim.air_mass, p.TANK_MASS)

    def test_impact_replay_matches_across_render_schedules(self):
        states = []
        for fps in (15, 30, 60, 120):
            sim = p.Simulation('flat', (parked(),))
            sim.z, sim.vz = -10., 40.
            advance(sim, 1., fps=fps)
            states.append((sim.x, sim.y, sim.z, sim.vx, sim.vy, sim.vz,
                           *sim.q, *sim.omega, sim.air_mass, sim.time))
        for state in states[1:]:
            self.assertEqual(state, states[0])


if __name__ == '__main__':
    unittest.main()
