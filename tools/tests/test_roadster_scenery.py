"""Geometry/budget and backdrop projection checks; not a badge timing test."""
import ast
import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'apps' / 'roadster_sandbox'
sys.path.insert(0, str(APP))
import fleet_models
from physics import raycast
import scene_palette as palette
import world
from distant_models import build_distant_model


class FakeMatrix:
    def translate(self, *args):
        return self
    def rotate_y_radians(self, angle):
        return self


class FakeScreen:
    def __init__(self):
        self.lines = []
        self.line_pens = []
    def clear(self):
        self.lines.clear()
        self.line_pens.clear()
    def rectangle(self, *args):
        pass
    def shape(self, *args):
        pass
    def line(self, x1, y1, x2, y2):
        # Native PicoVector clips before rasterization. This recorder models
        # only that geometric contract; native framebuffer checks are separate.
        def code(x, y):
            return (1 if x < 0. else 2 if x > 319. else 0) | (
                4 if y < 0. else 8 if y > 239. else 0)
        a, b = code(x1, y1), code(x2, y2)
        for _ in range(8):
            if not (a | b):
                self.lines.append((x1, y1, x2, y2))
                self.line_pens.append(self.pen)
                return
            if a & b:
                return
            out = a or b
            dx, dy = x2-x1, y2-y1
            if out & 8:
                x, y = x1+dx*(239.-y1)/dy, 239.
            elif out & 4:
                x, y = x1+dx*(-y1)/dy, 0.
            elif out & 2:
                x, y = 319., y1+dy*(319.-x1)/dx
            else:
                x, y = 0., y1+dy*(-x1)/dx
            if out == a:
                x1, y1, a = x, y, code(x, y)
            else:
                x2, y2, b = x, y, code(x, y)
        raise AssertionError('unbounded line clipping')


def load_scenery(path=None):
    screen = FakeScreen()
    badgeware = types.SimpleNamespace(screen=screen,
        color=types.SimpleNamespace(rgb=lambda *args: args),
        shape=types.SimpleNamespace(custom=lambda value: types.SimpleNamespace()),
        mat3=FakeMatrix)
    spec = importlib.util.spec_from_file_location('test_scenery_module', path or APP / 'scenery.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'badgeware': badgeware}):
        spec.loader.exec_module(module)
    return module, screen


def point(x, y, z):
    return types.SimpleNamespace(x=x, y=y, z=z)


def geometry_builders():
    """Load pure geometry output without imitating pico3d's native renderer."""
    source = ast.parse((APP / 'scene.py').read_text())
    names = {'rgb', 'floor_rect', '_cut_floor', 'airfield_mesh', 'lineup_mesh',
             'ground_mesh', 'surface_grid_mesh'}
    functions = [node for node in source.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    constants = ast.parse((APP / 'physics.py').read_text())
    namespace = {node.targets[0].id: ast.literal_eval(node.value)
                 for node in constants.body if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in {'LOOP_HALF_WIDTH', 'LOOP_Z'}}
    namespace.update({name: getattr(world, name) for name in
                      ('PAD_LEFT', 'PAD_RIGHT', 'PAD_HALF_DEPTH', 'RUNUP_X', 'ROW_START')})
    namespace.update(palette=palette, faces_mesh=lambda faces: faces)
    exec(compile(ast.Module(body=functions, type_ignores=[]),
                 str(APP / 'scene.py'), 'exec'), namespace)
    return namespace


def rgb565_luma(rgb):
    """Grayscale separation after the panel's RGB565 channel quantisation."""
    red, green, blue = rgb
    return (.2126 * (red >> 3) * 255 / 31 +
            .7152 * (green >> 2) * 255 / 63 +
            .0722 * (blue >> 3) * 255 / 31)


class SceneryTests(unittest.TestCase):
    def test_surface_grid_is_bounded_paint_with_nearby_scale_cues(self):
        builders = geometry_builders()
        faces, triangles = builders['surface_grid_mesh']()
        self.assertGreater(triangles, 0)
        self.assertLessEqual(triangles, 72, 'scale cues have a small fixed native budget')
        self.assertEqual(triangles, len(faces)*2)
        lines = ([], [])
        painted_area = 0.
        for points, shade in faces:
            self.assertEqual(len(points), 4)
            self.assertTrue(all(math.isfinite(value) for point in points for value in point))
            self.assertTrue(all(point[1] == 0. for point in points),
                            'paint stays on the ground; depth bias belongs to rendering')
            self.assertTrue(all(abs(point[axis]) <= 65. for point in points for axis in (0, 2)))
            x1, _, z1 = points[0]
            x2, _, z2 = points[2]
            width, depth = x2-x1, z2-z1
            self.assertGreater(min(width, depth), 0.)
            self.assertLessEqual(min(width, depth), .25, 'seams must not become broad bands')
            self.assertGreaterEqual(max(width, depth), 120.)
            painted_area += width*depth
            axis = 0 if width < depth else 1
            lines[axis].append(((x1+x2)/2 if axis == 0 else (z1+z2)/2,
                                min(width, depth)))
            rgb = shade & 255, shade >> 8 & 255, shade >> 16 & 255
            for floor in (palette.ASPHALT, palette.EARTH, palette.FIELD):
                self.assertGreater(rgb565_luma(rgb)-rgb565_luma(floor), 50.,
                                   'paint remains visible on pavement and both land colors')
        self.assertLess(painted_area, 128.*128.*.05,
                        'thin scale cues leave most of the floor unobscured')
        for direction in lines:
            direction.sort()
            self.assertGreaterEqual(direction[-1][0]-direction[0][0], 120.)
            for (first, _), (second, _) in zip(direction, direction[1:]):
                self.assertLessEqual(second-first, 8.00001,
                                     'nearby cues remain visible between the broad land parcels')
            self.assertGreater(max(width for _, width in direction),
                               min(width for _, width in direction)*1.5,
                               'major seams provide a second distance scale')

    def test_surface_grid_recycling_keeps_minor_and_major_world_phase(self):
        faces, _ = geometry_builders()['surface_grid_mesh']()

        def visible_paint(dx, dz):
            # Compare the shared interior of successive patches, including
            # both stroke widths. A swapped major/minor phase changes this set.
            result = set()
            for points, shade in faces:
                x1, z1 = max(-16., points[0][0]+dx), max(-16., points[0][2]+dz)
                x2, z2 = min(16., points[2][0]+dx), min(16., points[2][2]+dz)
                if x1 < x2 and z1 < z2:
                    result.add(tuple(round(value, 6) for value in (x1, z1, x2, z2))+(shade,))
            return result

        original = visible_paint(0., 0.)
        self.assertTrue(original)
        for dx, dz in ((32., 0.), (-32., 0.), (0., 32.), (0., -32.),
                       (32., 32.), (-32., -32.)):
            self.assertEqual(visible_paint(dx, dz), original,
                             'recycling must not make stationary ground markings slide')

    def test_surface_grid_is_not_a_raised_physical_obstacle(self):
        faces, _ = geometry_builders()['surface_grid_mesh']()
        # Query actual physics at painted seam centers and beside them. These
        # are visual scale cues, so wheels/fan still see the ordinary flat floor.
        # Native tests separately check decals are hidden behind cars and ramps.
        for points, _ in faces:
            x = (points[0][0]+points[2][0])/2
            z = (points[0][2]+points[2][2])/2
            for dx, dz in ((0., 0.), (.5, .5)):
                self.assertEqual(raycast((x+dx, 2., z+dz), (0., -1., 0.), 4., 'flat'),
                                 (2., (0., 1., 0.), 'ground'))

    def test_airfield_materials_tile_the_floor_without_depth_competing_faces(self):
        namespace = geometry_builders()
        for track in (False, True):
            faces, triangles = namespace['airfield_mesh'](track)
            rectangles = []
            for points, shade in faces:
                self.assertTrue(all(point[1] == 0. for point in points))
                rectangles.append((points[0][0], points[0][2],
                                   points[2][0], points[2][2]))
            for index, a in enumerate(rectangles):
                self.assertLess(a[0], a[2])
                self.assertLess(a[1], a[3])
                for b in rectangles[:index]:
                    overlap_x = min(a[2],b[2])-max(a[0],b[0])
                    overlap_z = min(a[3],b[3])-max(a[1],b[1])
                    self.assertFalse(overlap_x > 1e-9 and overlap_z > 1e-9,
                                     (track,a,b))
            area = sum((a[2]-a[0])*(a[3]-a[1]) for a in rectangles)
            footprint = 4.*namespace['LOOP_HALF_WIDTH'] if track else 0.
            self.assertAlmostEqual(area, 4000000.-footprint, places=6)
            self.assertLessEqual(triangles, 150)
            def covered(x,z):
                return any(a <= x <= c and b <= z <= d for a,b,c,d in rectangles)
            self.assertTrue(covered(0.,namespace['LOOP_Z']-4.), 'the approach remains visible')
            self.assertEqual(covered(0.,namespace['LOOP_Z']), not track,
                             'the ground-level track contact has no overlapping floor')

    def test_lineup_paint_and_rim_replace_floor_without_coplanar_overdraw(self):
        builders = geometry_builders()
        faces, triangles = builders['lineup_mesh']()
        self.assertLessEqual(triangles, 100)
        rectangles = []
        for points, shade in faces:
            self.assertTrue(all(point[1] == 0. for point in points))
            a = points[0][0], points[0][2], points[2][0], points[2][2]
            self.assertLess(a[0], a[2])
            self.assertLess(a[1], a[3])
            for b in rectangles:
                overlap_x = min(a[2], b[2]) - max(a[0], b[0])
                overlap_z = min(a[3], b[3]) - max(a[1], b[1])
                self.assertFalse(overlap_x > 1e-9 and overlap_z > 1e-9, (a, b))
            rectangles.append(a)
        self.assertAlmostEqual(sum((c-a)*(d-b) for a,b,c,d in rectangles),
                               4000000., places=6)
        rgb = builders['rgb']
        shades = {shade for _, shade in faces}
        self.assertTrue({rgb(*palette.ASPHALT), rgb(*palette.PAINT),
                         rgb(*palette.GUIDE)}.issubset(shades))

    def test_ground_and_navigation_palette_keep_grayscale_separation_on_rgb565(self):
        # These are readability floors, not claims about a particular panel's
        # physical brightness. They catch a return to nearly equal green values
        # while allowing future art direction to change the exact hues.
        for light, dark, difference in (
                ('EARTH', 'FIELD', 18.), ('EARTH', 'ASPHALT', 80.),
                ('PAINT', 'ASPHALT', 140.), ('SHOULDER', 'ASPHALT', 100.),
                ('EARTH', 'SURVEY', 35.), ('SURVEY', 'SURVEY_MAJOR', 20.),
                ('HAZE', 'RIDGE', 80.)):
            self.assertGreaterEqual(rgb565_luma(getattr(palette, light)) -
                                    rgb565_luma(getattr(palette, dark)), difference,
                                    '%s against %s' % (light, dark))
        builders = geometry_builders()
        floor, triangles = builders['ground_mesh'](4, 32)
        self.assertEqual(triangles, 32)
        self.assertEqual({shade for _, shade in floor},
                         {builders['rgb'](*palette.EARTH), builders['rgb'](*palette.FIELD)})

    def test_distant_fleet_has_finite_valid_indexed_geometry_and_remains_small(self):
        total_triangles = total_vertices = 0
        for entry in fleet_models.CATALOG:
            model = build_distant_model(entry['id'])
            total_triangles += model['triangles']
            total_vertices += model['vertices']
            for name, positions, indices, colours, pivot in model['parts']:
                self.assertTrue(all(math.isfinite(value) for value in positions))
                self.assertEqual(len(colours), len(positions) // 3)
                self.assertTrue(all(0 <= index < len(colours) for index in indices))
                xs, ys, zs = positions[::3], positions[1::3], positions[2::3]
                self.assertLessEqual(max(map(abs, xs)), entry['width'] / 2 + .01)
                self.assertGreaterEqual(min(ys), 0.)
                self.assertLessEqual(max(ys), entry['height'] + .01)
                self.assertLessEqual(max(map(abs, zs)), entry['length'] / 2 + .01)
                self.assertGreater(max(ys), entry['height'] * .8)
                self.assertGreater(max(zs)-min(zs), entry['length'] * .85)
        self.assertLess(total_triangles, 400)
        self.assertLess(total_vertices, 700)

    def test_near_meshes_and_far_budget_keep_every_parked_car_without_model_builds(self):
        spec = importlib.util.spec_from_file_location('test_fleet_scene_module', APP / 'fleet_scene.py')
        fleet = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
                'pico3d': types.SimpleNamespace(mat4=FakeMatrix),
                'model_assets': types.SimpleNamespace(static_native=lambda model: model)}):
            spec.loader.exec_module(fleet)
        fleet.set_quality('badge')
        sim = types.SimpleNamespace(x=0., y=1., z=0.)
        submitted = []
        def add(mesh, transform, vp):
            submitted.append(mesh)
        with patch.object(fleet, 'build_model', side_effect=AssertionError('rebuild in frame')), \
             patch.object(fleet.gc, 'collect', side_effect=AssertionError('GC in frame')):
            for x in range(-35, 36, 2):
                sim.x = x
                submitted.clear()
                fleet.draw(sim, None, add)
                self.assertEqual(len(submitted), 10)
                self.assertLessEqual(sum(model['detail'] != 'distant' for model in submitted), 2)
                self.assertLessEqual(fleet.submitted_triangles, fleet.BADGE_FLEET_TRIANGLES)
                self.assertLessEqual(fleet.submitted_vertices, fleet.BADGE_FLEET_VERTICES)
            sim.y = 100.
            fleet.draw(sim, None, add)
            self.assertEqual(fleet.submitted_triangles, fleet.DISTANT_FLEET_TRIANGLES)
        self.assertLess(fleet.BADGE_FLEET_TRIANGLES, fleet.LOW_FLEET_TRIANGLES * .4)
        self.assertLess(fleet.BADGE_FLEET_VERTICES, fleet.LOW_FLEET_VERTICES * .45)

    def test_horizon_tracks_camera_pitch_and_survives_any_altitude(self):
        scenery, screen = load_scenery()
        background = scenery.Background()
        for y in (25., 100., 500., 10000.):
            background.camera(point(0.,y+2.8,-11.),point(0.,y+.15,1.),50.)
            self.assertGreater(background.horizon_y, 40.)
            self.assertLess(background.horizon_y, 100.)
            background.draw()
            self.assertTrue(all(math.isfinite(value) and -0.0001 <= value <= 320.0001
                                for line in screen.lines for value in line))
        background.camera(point(0.,20.,0.),point(0.,20.,1.),50.)
        self.assertAlmostEqual(background.horizon_y,120.)
        background.camera(point(0.,20.,0.),point(0.,19.,5.),50.)
        self.assertLess(background.horizon_y,120.)
        background.camera(point(0.,20.,0.),point(0.,21.,5.),50.)
        self.assertGreater(background.horizon_y,120.)

    def test_terrain_grid_has_world_phase_and_clips_near_plane(self):
        scenery, screen = load_scenery()
        background = scenery.Background()
        frames = []
        for x,z in ((0.,0.),(64.,128.)):
            background.camera(point(x,44.,z-11.),point(x,41.35,z+1.),50.)
            background.draw()
            self.assertGreater(background.grid_lines,0)
            frames.append(tuple(screen.lines))
        self.assertEqual(len(frames[0]),len(frames[1]))
        for first,second in zip(frames[0],frames[1]):
            for a,b in zip(first,second):
                self.assertAlmostEqual(a,b,places=6)
        for yaw in range(0,360,17):
            a=math.radians(yaw)
            background.camera(point(0.,30.,0.),point(math.sin(a),29.8,math.cos(a)),50.)
            background.draw()
            self.assertTrue(all(-.0001 <= line[0] <= 319.0001 and
                -.0001 <= line[2] <= 319.0001 and -.0001 <= line[1] <= 239.0001 and
                -.0001 <= line[3] <= 239.0001 for line in screen.lines))

    def test_major_survey_lines_keep_world_phase_without_more_line_work(self):
        scenery, screen = load_scenery()
        background = scenery.Background()
        frames = []
        for x,z in ((0.,0.), (256.,256.)):
            background.camera(point(x,44.,z-11.), point(x,41.35,z+1.), 50.)
            background.draw()
            self.assertLessEqual(background.grid_lines, 24)
            self.assertEqual(set(screen.line_pens), {palette.SURVEY, palette.SURVEY_MAJOR})
            frames.append((tuple(screen.lines), tuple(screen.line_pens)))
        self.assertEqual(frames[0][1], frames[1][1])
        for first, second in zip(frames[0][0], frames[1][0]):
            for a, b in zip(first, second):
                self.assertAlmostEqual(a, b, places=6)


if __name__ == '__main__':
    unittest.main()
