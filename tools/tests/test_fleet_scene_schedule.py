"""Host checks for parked-model selection; native drawing is tested in WASM.

Only the scheduling function is loaded here. Opaque objects stand in for native
meshes/matrices, so these tests make no claim about Pico3D or badge performance.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[2] / 'apps/roadster_sandbox/fleet_scene.py'


def schedule(quality='badge'):
    entries = tuple((slug, x, 0., object()) for slug, x in
                    (('near', 0.), ('middle', 20.), ('edge', 65.), ('far', 66.)))
    builds = []

    def build_model(slug):
        builds.append(slug)
        return {'slug': slug, 'triangles': 200}

    namespace = {'quality': quality, '_parked_render': entries,
                 'low_models': {entry[0]: (object(), 100) for entry in entries},
                 'distant_models': {entry[0]: (object(), 30, 50, 120) for entry in entries},
                 'high_models': {}, 'build_model': build_model,
                 'static_native': lambda model: object(),
                 'gc': SimpleNamespace(collect=lambda: None)}
    tree = ast.parse(SOURCE.read_text())
    # Execute the production cache initializers against these opaque fixtures.
    # The scheduler now retains its ten distance slots and resolved tier/pose
    # metadata; extracting draw() alone would omit required module state.
    cache_names = {'_planar_distances', '_badge_render'}
    initializers = [node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id in cache_names
                            for target in node.targets)]
    assert len(initializers) == len(cache_names)
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'draw')
    exec(compile(ast.Module(body=initializers + [function], type_ignores=[]),
                 str(SOURCE), 'exec'), namespace)
    return namespace, entries, builds


class FleetScheduleTests(unittest.TestCase):
    def test_badge_preserves_all_nearby_models_and_reuses_their_poses(self):
        state, entries, builds = schedule()
        sim = SimpleNamespace(x=0., y=1., z=0.)
        submitted = []
        for _ in range(3):
            frame = []
            state['draw'](sim, None, lambda mesh, pose, vp: frame.append((mesh, pose)))
            submitted.append(frame)
        self.assertEqual(set(state['visible_ids']), {'near', 'middle', 'edge', 'far'})
        self.assertEqual(state['submitted_triangles'], 260)
        self.assertEqual(builds, [])
        for frame in submitted:
            self.assertEqual([pose for _, pose in frame], [entry[3] for entry in entries])
            self.assertEqual([mesh for mesh, _ in frame],
                             [state['low_models'][entry[0]][0] if entry[0] in ('near','middle')
                              else state['distant_models'][entry[0]][0] for entry in entries])

    def test_inspection_focus_keeps_distant_selected_car_visible(self):
        state, entries, builds = schedule()
        state['draw'](SimpleNamespace(x=-200., y=1., z=0.), None, lambda *args: None,
                      {'x': 66., 'z': 0.})
        self.assertEqual(set(state['visible_ids']), {'near', 'middle', 'edge', 'far'})
        self.assertEqual(state['submitted_triangles'], 260)
        self.assertEqual(builds, [])

    def test_full_quality_keeps_nearest_two_details_and_evicts_old_ones(self):
        state, entries, builds = schedule('full')
        sim = SimpleNamespace(x=0., y=1., z=0.)
        state['draw'](sim, None, lambda *args: None)
        self.assertEqual(set(state['high_models']), {'near', 'middle'})
        self.assertEqual(state['submitted_triangles'], 600)
        state['draw'](sim, None, lambda *args: None)
        self.assertEqual(builds, ['near', 'middle'])
        sim.x = 66.
        state['draw'](sim, None, lambda *args: None)
        self.assertEqual(set(state['high_models']), {'edge', 'far'})
        self.assertEqual(set(state['visible_ids']), {'near', 'middle', 'edge', 'far'})
        self.assertEqual(builds, ['near', 'middle', 'far', 'edge'])


if __name__ == '__main__':
    unittest.main()
