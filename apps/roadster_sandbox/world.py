"""One side-by-side obstacle row, shared by rendering and collision physics.

Parked cars face +Z. The main jump crosses their widths along +X, from the
shortest vehicle toward the tallest. Individual +Z approaches remain open.
"""
import math
from fleet_models import CATALOG

GAP = .7
_ORDER = sorted(CATALOG, key=lambda item: (item["height"], item["length"]))
ROW_LENGTH = sum(item["width"] for item in _ORDER) + GAP * (len(_ORDER) - 1)
ROW_START, ROW_END = -ROW_LENGTH / 2, ROW_LENGTH / 2
RUNUP_X, RUNUP_Z, RUNUP_YAW = ROW_START - 16., 0., math.pi / 2
PAD_LEFT, PAD_RIGHT, PAD_HALF_DEPTH = ROW_START - 26., ROW_END + 38., 22.


def _lineup():
    result, edge = [], ROW_START
    for entry in _ORDER:
        item = dict(entry)
        item["x"], item["z"], item["yaw"] = edge + item["width"] / 2, 0., 0.
        result.append(item)
        edge += item["width"] + GAP
    return tuple(result)


PARKED = _lineup()


def lineup_start(sim):
    """Reposition only when the caller explicitly starts/resets the scene."""
    sim.x, sim.z = RUNUP_X, RUNUP_Z
    sim.q = (math.cos(RUNUP_YAW / 2), 0., math.sin(RUNUP_YAW / 2), 0.)


def find_vehicle(slug):
    for item in PARKED:
        if item["id"] == slug:
            return item
    raise ValueError("unknown parked vehicle")


def nearest_vehicle(x, z):
    return min(PARKED, key=lambda item: (item["x"]-x)**2 + (item["z"]-z)**2)
