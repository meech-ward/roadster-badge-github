"""Round-trip the editable exports against their actual custom mesh inputs."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('tesla_export', ROOT / 'tools/export_models.py')
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


def read_obj(data):
    vertices, groups, faces = [], [], []
    group, material = None, None
    for line in data.decode().splitlines():
        tokens = line.split()
        if not tokens:
            continue
        if tokens[0] == 'v':
            vertices.append(tuple(float(v) for v in tokens[1:]))
        elif tokens[0] == 'g':
            group = tokens[1]
            groups.append(group)
        elif tokens[0] == 'usemtl':
            material = tokens[1]
        elif tokens[0] == 'f':
            faces.append((group, material, tuple(int(v)-1 for v in tokens[1:])))
    return vertices, groups, faces


def read_mtl(data):
    materials = {}
    material = None
    for line in data.decode().splitlines():
        tokens = line.split()
        if not tokens:
            continue
        if tokens[0] == 'newmtl':
            material = tokens[1]
        elif tokens[0] == 'Kd':
            materials[material] = tuple(round(float(v)*255) for v in tokens[1:])
    return materials


class TeslaExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = EXPORT.load_catalog()

    def test_all_eleven_models_round_trip_positions_indices_parts_and_colors(self):
        self.assertEqual(len(self.catalog), 11)
        for entry in self.catalog:
            with self.subTest(model=entry['id']):
                obj, mtl, metadata = EXPORT.encode_model(entry)
                vertices, groups, faces = read_obj(obj)
                materials = read_mtl(mtl)
                expected_vertices, expected_faces = [], []
                for name, position, index, colors, pivot in entry['model']['parts']:
                    offset = len(expected_vertices)
                    expected_vertices.extend(tuple(position[i+axis]+pivot[axis] for axis in range(3))
                                             for i in range(0, len(position), 3))
                    for i in range(0, len(index), 3):
                        ids = index[i:i+3]
                        color = colors[ids[0]]
                        rgb = (color & 255, (color >> 8) & 255, (color >> 16) & 255)
                        expected_faces.append((name, rgb, tuple(offset+j for j in ids)))
                self.assertEqual(vertices, expected_vertices)
                self.assertEqual(groups, [p[0] for p in entry['model']['parts']])
                self.assertEqual([(g, materials[m], indices) for g, m, indices in faces], expected_faces)
                self.assertEqual(len(vertices), metadata['vertices'])
                self.assertEqual(len(faces), metadata['triangles'])
                for exported, original in zip(metadata['parts'], entry['model']['parts']):
                    self.assertEqual(exported['pivot_m'], list(original[4]))
                    self.assertEqual(exported['vertices'], len(original[1])//3)
                    self.assertEqual(exported['triangles'], len(original[2])//3)

    def test_manifest_hashes_reproduce_the_exact_files_and_source(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            manifest = EXPORT.export_models(output)
            self.assertEqual(json.loads((output/'manifest.json').read_text()), manifest)
            self.assertEqual(len(list(output.glob('*.obj'))), 11)
            self.assertEqual(len(list(output.glob('*.mtl'))), 11)
            for model in manifest['models']:
                source = ROOT/model['source']['path']
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), model['source']['sha256'])
                for dependency in model['source'].get('dependencies', []):
                    source = ROOT/dependency['path']
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), dependency['sha256'])
                for format_name in ('obj', 'mtl'):
                    info = model['files'][format_name]
                    raw = (output/info['path']).read_bytes()
                    self.assertEqual(len(raw), info['bytes'])
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), info['sha256'])
                self.assertTrue(model['design_reference_urls'])
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            EXPORT.export_models(output)
            after = {p.name: p.read_bytes() for p in output.iterdir()}
            self.assertEqual(before, after)

    def test_unknown_selection_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'not-created'
            with self.assertRaisesRegex(ValueError, 'Unknown model'):
                EXPORT.export_models(output, ['missing'])
            self.assertFalse(output.exists())

    def test_export_rejects_silent_loss_of_interpolated_vertex_colors(self):
        entry = {'id': 'fixture', 'name': 'fixture', 'source': {'path': 'fixture.py', 'sha256': 'fixture'},
                 'design_reference_urls': [], 'model': {
                     'parts': [('body', [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2],
                                [0xff, 0xff00, 0xff0000], (0, 0, 0))],
                     'vertices': 3, 'triangles': 1, 'width': 1, 'height': 1, 'length': 0,
                     'wheel_radius': 0}}
        with self.assertRaisesRegex(ValueError, 'interpolated colours'):
            EXPORT.encode_model(entry)


if __name__ == '__main__':
    unittest.main()
