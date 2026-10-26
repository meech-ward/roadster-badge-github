#!/usr/bin/env python3
"""Export the handwritten Tesla meshes as editable, deterministic OBJ/MTL.

The two custom model modules and shared vehicle design are inputs. Wheel pivots are baked into
OBJ coordinates, then retained in manifest.json so editable parts can be put
back into their local space. No source pictures, third-party meshes, textures,
old Roadster generators, or previous reference exports are read.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'dist/models'
_SOURCES = ('apps/roadster_sandbox/roadster_model.py',
            'apps/roadster_sandbox/fleet_models.py')
_VEHICLE_SOURCE = 'apps/roadster_sandbox/vehicle_spec.py'
_NAME = re.compile(r'^[a-zA-Z0-9_-]+$')


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _number(value):
    """Round-trip Python doubles, without a timestamp or negative zero."""
    return '0' if value == 0 else format(value, '.17g')


def _material(color):
    return 'rgb_%02x%02x%02x' % (color & 255, (color >> 8) & 255, (color >> 16) & 255)


def _load_source(relative):
    path = ROOT / relative
    source = path.read_bytes()
    module = types.ModuleType(path.stem)
    module.__file__ = str(path)
    # Compile the exact bytes being hashed, even if another editor saves later.
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return module, {'path': relative, 'sha256': _sha256(source)}


def load_catalog():
    """Return models in display order, loading no app entry point or renderer."""
    vehicle, vehicle_source = _load_source(_VEHICLE_SOURCE)
    previous = sys.modules.get('vehicle_spec')
    sys.modules['vehicle_spec'] = vehicle
    try:
        roadster, roadster_source = _load_source(_SOURCES[0])
    finally:
        if previous is None:
            del sys.modules['vehicle_spec']
        else:
            sys.modules['vehicle_spec'] = previous
    roadster_source['dependencies'] = [vehicle_source]
    fleet, fleet_source = _load_source(_SOURCES[1])
    catalog = [{'id': 'roadster_2026', 'name': 'Roadster (October 2026)',
                'model': roadster.build_model(), 'source': roadster_source,
                'design_reference_urls': list(roadster.REFERENCE_URLS)}]
    for entry in fleet.CATALOG:
        catalog.append({'id': entry['id'], 'name': entry['name'],
                        'model': fleet.build_model(entry['id'], 'high'),
                        'source': fleet_source,
                        'design_reference_urls': list(fleet.REFERENCE_URLS)})
    return catalog


def _bounds(positions):
    low = [min(p[axis] for p in positions) for axis in range(3)]
    high = [max(p[axis] for p in positions) for axis in range(3)]
    return {'min': low, 'max': high,
            'extent': [high[i]-low[i] for i in range(3)]}


def _check_model(model):
    names = set()
    for name, positions, indices, colors, pivot in model['parts']:
        if not _NAME.fullmatch(name) or name in names:
            raise ValueError('Unsafe or duplicate OBJ part name: ' + name)
        names.add(name)
        if not positions or len(positions) % 3 or len(indices) % 3 or len(colors) != len(positions)//3:
            raise ValueError('Invalid mesh buffer lengths in ' + name)
        if len(pivot) != 3 or not all(math.isfinite(v) for v in positions + list(pivot)):
            raise ValueError('Non-finite position or invalid pivot in ' + name)
        if not all(isinstance(i, int) and 0 <= i < len(colors) for i in indices):
            raise ValueError('Invalid triangle index in ' + name)
        if not all(isinstance(c, int) and 0 <= c <= 0xFFFFFF for c in colors):
            raise ValueError('Invalid packed vertex colour in ' + name)
        for i in range(0, len(indices), 3):
            if len({colors[j] for j in indices[i:i+3]}) != 1:
                raise ValueError('A triangle has interpolated colours in ' + name)
    if model['triangles'] != sum(len(p[2])//3 for p in model['parts']):
        raise ValueError('Triangle metadata does not match model buffers')
    if model['vertices'] != sum(len(p[1])//3 for p in model['parts']):
        raise ValueError('Vertex metadata does not match model buffers')


def encode_model(entry):
    """Produce bytes and metadata without changing the source mesh buffers."""
    slug, model = entry['id'], entry['model']
    if not _NAME.fullmatch(slug):
        raise ValueError('Unsafe model id: ' + slug)
    _check_model(model)
    palette = sorted({c for part in model['parts'] for c in part[3]})
    obj = ['# Handwritten photo-based visual approximation; not manufacturer CAD.',
           '# Units: metres. +X left, +Y up, +Z front. Tyre floor: Y=0.',
           '# Each group retains source vertex order; wheel pivots are baked in.',
           '# Source: ' + entry['source']['path'],
           '# Source SHA-256: ' + entry['source']['sha256'],
           'mtllib ' + slug + '.mtl', 'o ' + slug, 's off']
    mtl = ['# Flat, texture-free colours from the custom source mesh.',
           '# Source packing: red | green << 8 | blue << 16 (0x00BBGGRR).']
    for color in palette:
        channels = (color & 255, (color >> 8) & 255, (color >> 16) & 255)
        diffuse = ' '.join(_number(c/255.0) for c in channels)
        mtl.extend(('', 'newmtl ' + _material(color),
                    '# packed_bbggrr 0x%06x' % color,
                    'Ka ' + diffuse, 'Kd ' + diffuse, 'Ks 0 0 0', 'd 1', 'illum 1'))
    offset, part_metadata, all_world = 0, [], []
    for name, positions, indices, colors, pivot in model['parts']:
        world = [tuple(positions[i+axis]+pivot[axis] for axis in range(3))
                 for i in range(0, len(positions), 3)]
        all_world.extend(world)
        obj.extend(('', 'g ' + name,
                    '# local_pivot_m ' + ' '.join(_number(v) for v in pivot)))
        obj.extend('v ' + ' '.join(_number(v) for v in p) for p in world)
        material = None
        for i in range(0, len(indices), 3):
            ids = indices[i:i+3]
            color = colors[ids[0]]
            if color != material:
                obj.append('usemtl ' + _material(color))
                material = color
            obj.append('f ' + ' '.join(str(index+offset+1) for index in ids))
        count = len(positions)//3
        part_metadata.append({'name': name, 'vertices': count,
                              'triangles': len(indices)//3,
                              'pivot_m': list(pivot),
                              'obj_vertices_1_based': [offset+1, offset+count],
                              'world_bounds_m': _bounds(world)})
        offset += count
    obj_bytes = ('\n'.join(obj) + '\n').encode('utf-8')
    mtl_bytes = ('\n'.join(mtl) + '\n').encode('utf-8')
    metadata = {
        'id': slug, 'name': entry['name'], 'detail': 'high',
        'triangles': model['triangles'], 'vertices': model['vertices'],
        'source': entry['source'],
        'design_reference_urls': entry['design_reference_urls'],
        'bounds_m': _bounds(all_world),
        'declared_dimensions_m': {key: model[key] for key in ('length', 'width', 'height')},
        'wheel_radius_m': model['wheel_radius'], 'parts': part_metadata,
        'materials': [{'name': _material(c), 'packed_bbggrr': '0x%06x' % c,
                       'rgb': [c & 255, (c >> 8) & 255, (c >> 16) & 255]}
                      for c in palette],
        'files': {'obj': {'path': slug+'.obj', 'bytes': len(obj_bytes), 'sha256': _sha256(obj_bytes)},
                  'mtl': {'path': slug+'.mtl', 'bytes': len(mtl_bytes), 'sha256': _sha256(mtl_bytes)}},
    }
    return obj_bytes, mtl_bytes, metadata


def export_models(output=DEFAULT_OUTPUT, model_ids=None):
    """Write a deterministic set; reject unknown selections before any writes."""
    catalog = load_catalog()
    available = {entry['id'] for entry in catalog}
    selected = available if model_ids is None else set(model_ids)
    unknown = selected-available
    if unknown:
        raise ValueError('Unknown model id(s): ' + ', '.join(sorted(unknown)))
    if not selected:
        raise ValueError('Select at least one model')
    encoded = [(entry['id'], encode_model(entry)) for entry in catalog if entry['id'] in selected]
    manifest = {
        'schema_version': 1,
        'title': 'Custom Tesla model sources and editable meshes',
        'provenance': 'All geometry is generated from the two handwritten Python model modules. '
                      'Official product photographs were visual design references only; no imported '
                      'mesh, texture or previous Roadster model geometry is used by this exporter.',
        'accuracy': 'Photo-based visual approximations for a small display, not manufacturer CAD. '
                    'Declared dimensions are artistic envelopes; bounds record the actual exported vertices.',
        'scope': 'Geometry exports only. This manifest makes no claim about physics provenance, '
                 'vehicle engineering accuracy, production specifications or hardware validation.',
        'coordinates': {'units': 'metres', 'x': 'left', 'y': 'up', 'z': 'front',
                        'ground_y': 0, 'obj_pivots': 'baked into vertices; per-part pivots retained below'},
        'format': {'geometry': 'triangulated Wavefront OBJ', 'materials': 'Wavefront MTL, flat diffuse RGB',
                   'color_packing': 'source red | green << 8 | blue << 16; material names use RRGGBB',
                   'textures': False},
        'generator': {'path': 'tools/export_models.py',
                      'sha256': _sha256(Path(__file__).read_bytes())},
        'model_count': len(encoded), 'models': [data[2] for slug, data in encoded],
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for slug, (obj, mtl, metadata) in encoded:
        (output / (slug+'.obj')).write_bytes(obj)
        (output / (slug+'.mtl')).write_bytes(mtl)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT,
                        help='output folder (default: dist/models)')
    parser.add_argument('--model', action='append', dest='model_ids',
                        help='export this model id; repeat to select multiple (default: all eleven)')
    args = parser.parse_args()
    try:
        manifest = export_models(args.output, args.model_ids)
    except ValueError as error:
        parser.error(str(error))
    print('Exported %d models to %s' % (manifest['model_count'], args.output))
    for model in manifest['models']:
        print('%s: %d triangles, %d vertices, %d editable parts' %
              (model['id'], model['triangles'], model['vertices'], len(model['parts'])))


if __name__ == '__main__':
    main()
