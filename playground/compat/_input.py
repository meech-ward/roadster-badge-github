"""Synthetic Universe logical inputs. Values are tokens, never real GPIOs."""
import json
import sim_host

ticks = 0
ticks_delta = 0
pressed = ()
held = ()
released = ()
changed = ()
previous = set()
sources = {"buttons": (), "pad": ()}
source_edges = {"buttons": ((), (), (), ()), "pad": ((), (), (), ())}
imu_value = (0, 0, 16384, 0, 0, 0)
_order = ("HOME", "LEFT", "RIGHT", "SELECT", "BACK", "MENU", "UP", "DOWN")

def poll():
    global ticks, ticks_delta, pressed, held, released, changed, previous, imu_value
    state = json.loads(sim_host.inputs())
    now = int(state["ticks"])
    ticks_delta = now - ticks
    ticks = now
    for source in ("buttons", "pad"):
        old = set(sources[source])
        new = set(state.get(source, ()))
        sources[source] = tuple(k for k in _order if k in new)
        source_edges[source] = (
            tuple(k for k in _order if k in new and k not in old),
            sources[source],
            tuple(k for k in _order if k in old and k not in new),
            tuple(k for k in _order if (k in old) != (k in new)),
        )
    down = set(sources["buttons"]) | set(sources["pad"])
    held = tuple(k for k in _order if k in down)
    pressed = tuple(k for k in _order if k in down and k not in previous)
    released = tuple(k for k in _order if k in previous and k not in down)
    changed = tuple(k for k in _order if (k in previous) != (k in down))
    previous = down
    imu_value = tuple(state.get("imu", imu_value))
