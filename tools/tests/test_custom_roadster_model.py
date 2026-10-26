"""Geometry and app-interface checks for the independently authored Roadster.

These are host mesh checks, not a substitute for the native badge render tests.
"""

import ast
import importlib.util
import math
from pathlib import Path
import sys
import unittest


SOURCE = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox/roadster_model.py'
SPEC = importlib.util.spec_from_file_location('custom_roadster_model', SOURCE)
MODEL = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SOURCE.parent))
try:
    SPEC.loader.exec_module(MODEL)
finally:
    sys.path.pop(0)


def points(part):
    p = part[1]
    return [tuple(p[i:i+3]) for i in range(0, len(p), 3)]


def triangles(part):
    p = points(part)
    index = part[2]
    return [tuple(p[j] for j in index[i:i+3]) for i in range(0, len(index), 3)]


def normal(t):
    a = tuple(t[1][i] - t[0][i] for i in range(3))
    b = tuple(t[2][i] - t[0][i] for i in range(3))
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def projected_x(t, y, z):
    """X intersection of a ray with a non-edge-on triangle, or None."""
    a, b, c = t
    determinant = (b[2]-c[2])*(a[1]-c[1]) + (c[1]-b[1])*(a[2]-c[2])
    if abs(determinant) < 1e-10:
        return None
    u = ((b[2]-c[2])*(y-c[1]) + (c[1]-b[1])*(z-c[2])) / determinant
    v = ((c[2]-a[2])*(y-c[1]) + (a[1]-c[1])*(z-c[2])) / determinant
    w = 1-u-v
    if min(u, v, w) < -1e-8:
        return None
    return u*a[0]+v*b[0]+w*c[0]


class CustomRoadsterModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = MODEL.build_model()
        cls.parts = {part[0]: part for part in cls.model['parts']}

    def test_portable_module_has_no_runtime_or_asset_dependency(self):
        tree = ast.parse(SOURCE.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        self.assertEqual(imports, ['math', 'vehicle_spec'])
        self.assertTrue(all(url.startswith('https://www.tesla.com/') or
                            url.startswith('https://digitalassets.tesla.com/')
                            for url in MODEL.REFERENCE_URLS))

    def test_part_contract_and_wheel_order(self):
        self.assertEqual(list(self.parts), ['body', 'glass', 'trim', 'details', 'nozzles',
                                          'wheel_FL', 'wheel_FR', 'wheel_RL', 'wheel_RR'])
        wheels = self.model['parts'][-4:]
        self.assertEqual([p[4] for p in wheels], list(MODEL.WHEEL_CENTRES))
        # Wheel order is part of steering and suspension, not merely naming.
        self.assertTrue(all(p[4][2] > 0 for p in wheels[:2]))
        self.assertTrue(all(p[4][2] < 0 for p in wheels[2:]))
        self.assertTrue(all(p[4][0] > 0 for p in wheels[::2]))
        self.assertTrue(all(p[4][0] < 0 for p in wheels[1::2]))
        self.assertTrue(all(p[4] == (0, 0, 0) for p in self.model['parts'][:-4]))
        self.assertEqual(self.model['wheel_radius'], MODEL.WHEEL_RADIUS)

    def test_counts_bounds_and_native_index_budget(self):
        self.assertEqual(self.model['triangles'], sum(len(p[2])//3 for p in self.model['parts']))
        self.assertEqual(self.model['vertices'], sum(len(p[1])//3 for p in self.model['parts']))
        self.assertGreaterEqual(self.model['triangles'], 1600)
        self.assertLessEqual(self.model['triangles'], 2200)
        world = [tuple(v[i]+part[4][i] for i in range(3))
                 for part in self.model['parts'] for v in points(part)]
        low = [min(v[i] for v in world) for i in range(3)]
        high = [max(v[i] for v in world) for i in range(3)]
        self.assertAlmostEqual(low[1], 0)
        for key, axis in (('width', 0), ('height', 1), ('length', 2)):
            self.assertAlmostEqual(self.model[key], high[axis]-low[axis])
        self.assertTrue(4.5 < self.model['length'] < 4.9)
        self.assertTrue(2.0 < self.model['width'] < 2.2)
        self.assertTrue(1.10 < self.model['height'] < 1.25)
        self.assertTrue(all(len(p[1])//3 < 65536 for p in self.model['parts']))

    def test_all_triangles_are_finite_indexed_and_non_degenerate(self):
        for name, position, index, color, pivot in self.model['parts']:
            with self.subTest(part=name):
                self.assertEqual(len(position) % 3, 0)
                self.assertEqual(len(index) % 3, 0)
                self.assertEqual(len(color), len(position)//3)
                self.assertTrue(all(math.isfinite(v) for v in position))
                self.assertTrue(all(isinstance(i, int) and 0 <= i < len(color) for i in index))
                self.assertTrue(all(isinstance(c, int) and 0 <= c <= 0xFFFFFF for c in color))
                for i, tri in enumerate(triangles(self.parts[name])):
                    n = normal(tri)
                    self.assertGreater(sum(v*v for v in n), 1e-18, (name, i))
                    self.assertEqual(len({color[j] for j in index[i*3:i*3+3]}), 1)

    def test_wheel_openings_really_clear_the_tyres(self):
        body = triangles(self.parts['body'])
        samples = 0
        # Ray-test inside the circular side profile, rather than merely checking
        # arch vertices: a hidden body triangle across an opening must fail.
        for centre in (-MODEL.WHEELBASE*.5, MODEL.WHEELBASE*.5):
            spacing = (MODEL.WHEEL_RADIUS-.025)/7.
            for iy in range(-7, 8):
                for iz in range(-7, 8):
                    dy, dz = iy*spacing, iz*spacing
                    if dy*dy+dz*dz > (MODEL.WHEEL_RADIUS-.025)**2:
                        continue
                    y, z = MODEL.WHEEL_RADIUS+dy, centre+dz
                    for tri in body:
                        x = projected_x(tri, y, z)
                        if x is not None:
                            self.assertFalse(.70 < abs(x) < 1.10,
                                             ('body crosses tyre silhouette', x, y, z))
                    samples += 1
        self.assertGreater(samples, 250)

    def test_tyre_tread_winding_and_floor_contact(self):
        for name in MODEL.WHEEL_NAMES:
            part = self.parts[name]
            p = points(part)
            self.assertAlmostEqual(min(v[1] for v in p), -MODEL.WHEEL_RADIUS)
            self.assertAlmostEqual(max(v[1] for v in p), MODEL.WHEEL_RADIUS)
            self.assertAlmostEqual(min(v[2] for v in p), -MODEL.WHEEL_RADIUS)
            self.assertAlmostEqual(max(v[2] for v in p), MODEL.WHEEL_RADIUS)
            tread_faces = 0
            for tri in triangles(part):
                if all(math.hypot(v[1], v[2]) >= MODEL.WHEEL_RADIUS-.045-1e-8 for v in tri) and \
                        max(v[0] for v in tri)-min(v[0] for v in tri) > .01:
                    n = normal(tri)
                    radial = (sum(v[1] for v in tri), sum(v[2] for v in tri))
                    self.assertGreater(n[1]*radial[0]+n[2]*radial[1], 0)
                    tread_faces += 1
            self.assertGreaterEqual(tread_faces, 90)

    def test_body_sides_and_top_face_outward(self):
        sides, top = 0, 0
        for tri in triangles(self.parts['body']):
            n = normal(tri)
            if all(abs(v[0]) > .92 for v in tri) and abs(n[0]) > 1e-8:
                self.assertGreater(n[0]*sum(v[0] for v in tri), 0)
                sides += 1
            if all(abs(v[0]) < .70 and v[1] > .60 for v in tri):
                self.assertGreaterEqual(n[1], -1e-8)
                top += 1
        self.assertGreater(sides, 50)
        self.assertGreater(top, 20)

    def test_visible_body_and_glass_are_bilaterally_symmetric(self):
        for name in ('body', 'glass'):
            vertices = {tuple(round(v, 7) for v in p) for p in points(self.parts[name])}
            reflected = {(-p[0], p[1], p[2]) for p in vertices}
            self.assertEqual(vertices, reflected, name)

    def test_new_roadster_silhouette_and_glass_landmarks(self):
        body = points(self.parts['body'])
        glass = points(self.parts['glass'])
        self.assertLess(max(v[1] for v in body if v[2] > 2.22), .60)
        roof = [v for v in glass if v[1] > 1.10]
        self.assertGreater(len(roof), 4)
        self.assertTrue(all(-.7 < v[2] < .4 for v in roof))
        self.assertTrue(any(v[2] < -1.2 and v[1] > .88 for v in glass))
        colors = self.parts['glass'][3]
        # The independently chosen canopy material is bronze, not sky blue.
        self.assertTrue(all((c & 255) >= ((c >> 8) & 255) >= (c >> 16) for c in colors))

    def test_headlight_lenses_are_visible_above_the_fenders(self):
        detail = self.parts['details']
        body = triangles(self.parts['body'])
        light_faces = 0
        for i, tri in enumerate(triangles(detail)):
            color = detail[3][detail[2][i*3]]
            rgb = (color & 255, (color >> 8) & 255, color >> 16)
            if not (min(v[2] for v in tri) > 1.8 and max(v[2] for v in tri) < 2.1 and
                    rgb[0] > 210 and rgb[1] > 235 and rgb[2] > 245):
                continue
            x, y, z = (sum(v[k] for v in tri)/3 for k in range(3))
            # Cast a vertical ray into the actual painted mesh. A lamp below
            # that skin is numerically valid geometry but invisible natively.
            heights = []
            for panel in body:
                height = projected_x([(v[1], v[0], v[2]) for v in panel], x, z)
                if height is not None:
                    heights.append(height)
            self.assertTrue(heights)
            self.assertGreater(y-max(heights), .005)
            light_faces += 1
        self.assertEqual(light_faces, 4)

    def test_repeat_build_has_fresh_buffers_and_same_geometry(self):
        other = MODEL.build_model()
        self.assertEqual(other, self.model)
        other['parts'][0][1][0] += 100
        self.assertNotEqual(other['parts'][0][1][0], self.model['parts'][0][1][0])


if __name__ == '__main__':
    unittest.main()
