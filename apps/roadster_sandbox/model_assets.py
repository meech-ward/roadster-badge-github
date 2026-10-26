"""Convert our handwritten vehicle geometry to the native badge mesh API."""
from array import array
import pico3d


def native_parts(model, origin_y=0.):
    result = []
    for name, positions, indices, colours, pivot in model["parts"]:
        points = array("f", positions)
        wheel = name.startswith("wheel_")
        if origin_y and not wheel:
            for i in range(1, len(points), 3):
                points[i] += origin_y
        mesh = pico3d.mesh(positions=points, indices=array("H", indices),
                           colors=array("I", colours), quantise=True)
        result.append((name, mesh, (pivot[0], pivot[1] + origin_y if wheel else 0., pivot[2])))
    return tuple(result)


def static_native(model):
    """Bake parked wheels into one mesh; their pivots still exist in the model."""
    points, indices, colours = array("f"), array("H"), array("I")
    for name, vertices, faces, shades, pivot in model["parts"]:
        base = len(points) // 3
        for i in range(0, len(vertices), 3):
            points.extend((vertices[i] + pivot[0], vertices[i+1] + pivot[1],
                           vertices[i+2] + pivot[2]))
        indices.extend(index + base for index in faces)
        colours.extend(shades)
    return pico3d.mesh(positions=points, indices=indices, colors=colours,
                       quantise=True)
