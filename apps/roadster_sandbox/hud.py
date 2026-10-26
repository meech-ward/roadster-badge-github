"""Compact top-only 320x240 Roadster instruments using native primitives.

The world occupies y=50..239, with no footer. Speed, delivered rear boost,
fan ON/OFF, sensed tilt and the physical nozzle map stay in the top strip.
The fan tile is aligned near clicky Enter above the inverted gamepad display.
Temporary control help and scene selection are separate overlays.

Returned sensor/force telemetry also includes diagnostic contact and stunt
state; these do not create extra labels over the world. Drawing never changes
controls, simulation or timers. Retained regions skip identical pixels.
"""
from badgeware import *
from physics import NOZZLES
from motion import TILT_FULL_DEG

WIDTH = 320
HEIGHT = 240
WORLD_RECT = (0, 50, 320, 190)
INK = color.rgb(9, 17, 29)
PANEL = color.rgb(30, 46, 64)
BODY = color.rgb(83, 102, 121)
WHITE = color.rgb(246, 250, 255)
MUTED = color.rgb(156, 179, 197)
DIM = color.rgb(66, 87, 105)
CYAN = color.rgb(78, 234, 224)
GOLD = color.rgb(255, 192, 83)
PINK = color.rgb(248, 127, 208)

# Seven-segment numerals remain legible without depending on font scaling.
SEGMENTS = ((0, 1, 2, 4, 5, 6), (2, 5), (0, 2, 3, 4, 6),
            (0, 2, 3, 5, 6), (1, 2, 3, 5), (0, 1, 3, 5, 6),
            (0, 1, 3, 4, 5, 6), (0, 2, 5), (0, 1, 2, 3, 4, 5, 6),
            (0, 1, 2, 3, 5, 6))
SEGMENT_RECTS = ((2, 0, 11, 3), (0, 2, 3, 11), (12, 2, 3, 11),
                 (2, 12, 11, 3), (0, 14, 3, 11), (12, 14, 3, 11),
                 (2, 24, 11, 3))

# Body axes are +X left, +Z forward. Nose points toward the top of the display.
# Preserve the physical nozzle layout rather than labelling unrelated numbers.
# Fit the mount span once, reserving six pixels below it for rear exhaust.
# Vehicle geometry can change without allowing jets to paint over the world.
_JET_Z_SCALE = 17./max(abs(n[1][2]) for n in NOZZLES)
NOZZLE_POINTS = tuple((int(290 - n[1][0] * 12), int(26 - n[1][2] * _JET_Z_SCALE))
                      for n in NOZZLES)
_regions = {}
_jets_cache = (None, [])


def invalidate():
    """Call after any other drawing overlaps the retained instrument bars."""
    _regions.clear()


def _region(name, value, bounds, preserve):
    if preserve and _regions.get(name) == value:
        return False
    _regions[name] = value
    _rect(*bounds, INK)
    return True


def _rect(x, y, width, height, pen):
    screen.pen = pen
    screen.rectangle(x, y, width, height)


def _text(value, x, y, pen=WHITE):
    screen.pen = pen
    screen.text(value, x, y)


def _digit(number, x, y, pen):
    for i in SEGMENTS[number]:
        dx, dy, width, height = SEGMENT_RECTS[i]
        _rect(x + dx, y + dy, width, height, pen)


def _jet_pixels(forces):
    """Encode only the visible on/off dots and integer exhaust lengths.

    Lift dots have no strength-dependent size; rear and side lines have four
    possible lengths. Force changes within the same pixels need no redraw.
    Zero always means inactive, so this also preserves the active-jet readout.
    Ten direct expressions avoid a per-frame list, loop and append calls.
    """
    return (0 if forces[0] <= 1. else 1 + int(3 * min(1., forces[0] / NOZZLES[0][3])),
            0 if forces[1] <= 1. else 1 + int(3 * min(1., forces[1] / NOZZLES[1][3])),
            0 if forces[2] <= 1. else 1 + int(3 * min(1., forces[2] / NOZZLES[2][3])),
            0 if forces[3] <= 1. else 1 + int(3 * min(1., forces[3] / NOZZLES[3][3])),
            0 if forces[4] <= 1. else 1,
            0 if forces[5] <= 1. else 1,
            0 if forces[6] <= 1. else 1,
            0 if forces[7] <= 1. else 1,
            0 if forces[8] <= 1. else 2 + int(3 * min(1., forces[8] / NOZZLES[8][3])),
            0 if forces[9] <= 1. else 2 + int(3 * min(1., forces[9] / NOZZLES[9][3])))


def _jet_map(pixels):
    _rect(282, 10, 16, 32, BODY)
    _rect(284, 6, 12, 6, BODY)
    _rect(284, 18, 12, 14, INK)
    _rect(278, 16, 3, 8, MUTED)
    _rect(299, 16, 3, 8, MUTED)
    _rect(278, 32, 3, 8, MUTED)
    _rect(299, 32, 3, 8, MUTED)
    _rect(287, 8, 6, 2, WHITE)
    for i in range(10):
        x, y = NOZZLE_POINTS[i]
        length = pixels[i]
        if not length:
            _rect(x - 1, y - 1, 2, 2, DIM)
            continue
        pen = GOLD if i < 4 else CYAN if i < 8 else PINK
        _rect(x - 1, y - 1, 3, 3, pen)
        if i < 4:
            _rect(x, y + 2, 1, length, pen)
        elif i >= 8:
            _rect(x + 2 if i == 8 else x - length - 1, y, length, 1, pen)


def _tilt_gauge(motion, level, preserve, committed_flip=False, ground_look=False):
    """Small retained sensor instrument, never a source of control commands.

    Marker position is the measured neutral-relative angle, saturated at full
    tilt. Percentages are the calibrated proportional input, in 5% increments;
    they are diagnostic values, not delivered thrust. Whole-pixel cache keys avoid
    redrawing for invisible sensor noise. Invalid samples never show old tilt.
    """
    get = motion.get
    calibrated = bool(get('calibrated'))
    clipped = bool(get('sensor_clipped'))
    valid = calibrated and bool(get('tilt_valid')) and not clipped
    armed = bool(get('armed'))
    angle_x, angle_y = get('angle_x', 0.), get('angle_y', 0.)
    tilt_x, tilt_y = get('tilt_x', 0.), get('tilt_y', 0.)
    if valid:
        # Two direct expressions per pair: no per-frame generator objects.
        marker = (int(max(-1., min(1., angle_x / TILT_FULL_DEG)) * 12),
                  int(max(-1., min(1., angle_y / TILT_FULL_DEG)) * 12))
        percent = (int(max(0., min(1., abs(tilt_x))) * 20 + .5) * (5 if tilt_x >= 0. else -5),
                   int(max(0., min(1., abs(tilt_y))) * 20 + .5) * (5 if tilt_y >= 0. else -5))
    else:
        marker, percent = None, (None, None)
    level_state = level.get('level_state', 'off')
    strength = int(max(0., min(1., level.get('level_strength', 0.))) * 20 + .5) * 5
    if not calibrated:
        status = 'CENTER'
    elif clipped:
        status = 'CLIP'
    elif not valid:
        status = 'WAIT'
    elif committed_flip:
        status = 'FLIP'
    elif ground_look:
        status = 'LOOK'
    elif not armed:
        status = 'SENSE'
    elif get('flick_active'):
        status = 'FLICK'
    elif level_state == 'level':
        status = 'LEVEL'
    elif level_state == 'steady':
        status = 'STEADY'
    elif get('mode') == 'flick':
        status = 'FLICK ONLY'
    elif level_state == 'tilt':
        status = 'TILT'
    else:
        status = 'READY'
    # The square expresses both direction and amount. Keep the precise signed
    # percentages in diagnostics without filling the game view with numbers.
    label = 'FLICK' if status == 'FLICK ONLY' else status
    pixels = (marker, label, armed, valid)
    if _region('tilt', pixels, (206, 0, 65, 50), preserve):
        pen = CYAN if status in ('LOOK', 'LEVEL', 'STEADY') else GOLD if status in ('FLICK', 'FLIP', 'CLIP') else MUTED
        _text(label, 212, 1, pen)
        _rect(213, 15, 31, 31, PANEL)
        _rect(214, 16, 29, 29, INK)
        _rect(216, 30, 25, 1, DIM)
        _rect(228, 18, 1, 25, DIM)
        _rect(226, 28, 5, 5, PANEL)
        _rect(228, 30, 1, 1, MUTED)
        if marker is not None:
            pen = GOLD if status in ('FLICK', 'FLIP') else CYAN if armed or ground_look else MUTED
            _rect(227 + marker[0], 29 - marker[1], 3, 3, pen)
    return {'status': status, 'valid': valid, 'armed': armed, 'clipped': clipped,
            'angles': (angle_x, angle_y) if valid else None,
            'tilt': (tilt_x, tilt_y) if valid else None,
            'percent': percent, 'marker': marker, 'level_strength': strength,
            'source': 'sensor', 'bounds': (206, 0, 65, 50)}


def draw(sim, feedback, stunt=None, state=None, preserve=False):
    """Draw the compact top strip; the world owns every pixel below it.

    preserve=True requires world drawing clipped to WORLD_RECT and explicit
    invalidation after overlays. Each region is keyed by its visible pixels.
    """
    global _jets_cache
    state = state or {}
    stunt = stunt or {}
    screen.font = font.sins
    speed_kmh = max(0., feedback.speed_mph * 1.609344)
    shown_speed = min(999, int(speed_kmh))
    boost = max(0., min(1., feedback.rear_fraction))
    boosting = feedback.rear_force > 100.
    if _region('speed_label', True, (0, 0, 64, 50), preserve):
        _regions.pop('speed0', None)
        _regions.pop('speed1', None)
        _regions.pop('speed2', None)
        _text('KM/H', 17, 36, MUTED)
    speed_pen = GOLD if boosting else WHITE
    digit = shown_speed // 100
    if _region('speed0', (digit, boosting), (7, 6, 15, 27), preserve):
        _digit(digit, 7, 6, speed_pen)
    digit = shown_speed // 10 % 10
    if _region('speed1', (digit, boosting), (26, 6, 15, 27), preserve):
        _digit(digit, 26, 6, speed_pen)
    digit = shown_speed % 10
    if _region('speed2', (digit, boosting), (45, 6, 15, 27), preserve):
        _digit(digit, 45, 6, speed_pen)

    # Clicky Enter lies above the left side in the inverted gamepad grip.
    # A small edge marker associates this indicator with that physical button.
    fan_on = bool(state.get('fan_on'))
    fan_state = 'GRIPPING' if fan_on and sim.fan_force > 100. else 'NO SEAL' if fan_on else 'OFF'
    if _region('fan', fan_on, (64, 0, 44, 50), preserve):
        _rect(77, 0, 7, 2, CYAN if fan_on else DIM)
        _rect(66, 5, 40, 40, CYAN if fan_on else PANEL)
        pen = INK if fan_on else MUTED
        _text('FAN', 76, 9, pen)
        _text('ON' if fan_on else 'OFF', 76, 26, pen)

    boost_length = max(1, int(84 * boost)) if boost > 0. else 0
    boost_percent = int(boost * 100. + .5)
    if _region('boost', (boost_percent, boost_length), (108, 0, 98, 50), preserve):
        _text('BOOST', 115, 7, GOLD if boost_length else MUTED)
        _rect(114, 22, 84, 5, PANEL)
        if boost_length:
            _rect(114, 22, boost_length, 5, GOLD)
        _text('%d%%' % boost_percent, 115, 34, GOLD if boost_length else MUTED)

    motion = state.get('motion')
    # Laptop play has no badge sensor stream; leave its small slot empty.
    if motion is None:
        if _regions.get('tilt') != 'none' or not preserve:
            _region('tilt', 'none', (206, 0, 65, 50), False)
        tilt_gauge = None
    else:
        tilt_gauge = _tilt_gauge(motion, state.get('level') or {}, preserve,
                                 state.get('committed_flip', False), state.get('ground_look', False))
    jet_pixels = _jet_pixels(sim.nozzle_forces)
    if jet_pixels != _jets_cache[0]:
        _jets_cache = (jet_pixels, [i for i in range(10) if jet_pixels[i]])
    if _region('jets', jet_pixels, (271, 0, 49, 50), preserve):
        _jet_map(jet_pixels)
    _rect(0, 49, WIDTH, 1, PANEL)

    # Diagnostic state remains accessible without adding a persistent footer.
    grounded = bool(sim.contact_count or sim.chassis_contacts)
    up = sim.basis()[1][1]
    status = ('INVERTED' if up < -.35 else 'AIRBORNE' if not grounded else
              'BODY CONTACT' if sim.chassis_contacts else 'GROUNDED')
    return {'speed_kmh': speed_kmh, 'active_nozzles': _jets_cache[1],
            'fan_on': fan_on, 'fan_state': fan_state, 'fan_bounds': (64, 0, 44, 50),
            'status': status, 'hint_lines': (),
            'stunt_message': stunt.get('message') or '', 'boost_fraction': boost,
            'tilt_gauge': tilt_gauge, 'world_rect': WORLD_RECT}


def draw_help():
    """Draw the game's source-specific control map; does not pause or mutate."""
    invalidate()
    screen.font = font.sins
    _rect(0, 0, WIDTH, HEIGHT, INK)
    _text('ROADSTER / CONTROLS', 10, 9, WHITE)
    _text('PHYSICS STAYS LIVE', 204, 10, CYAN)
    _rect(14, 29, 292, 66, PANEL)

    # Touch layout in the inverted gamepad grip, matching physical positions.
    for x, y, label in ((47, 37, 'U'), (29, 55, 'L'),
                        (65, 55, 'R'), (47, 73, 'D')):
        _rect(x, y, 15, 15, BODY)
        _text(label, x + 5, y + 4, WHITE)
    _text('DRIVE', 91, 58, MUTED)
    for x, label in ((138, 'MENU'), (175, 'SCENE')):
        _rect(x, 53, 26, 16, BODY)
        _text(label, x + 2, 58, WHITE)
    _rect(220, 50, 31, 22, CYAN)
    _text('LIFT', 225, 58, INK)
    _text('BACK', 224, 80, MUTED)
    _rect(263, 50, 34, 22, GOLD)
    _text('BOOST', 266, 58, INK)
    _text('SELECT', 263, 80, MUTED)

    rows = (('TOUCH DPAD', 'DRIVE / STEER / BRAKE'),
            ('OUTER RIGHT', 'BOOST + SELECT IN MENUS'),
            ('INNER RIGHT', 'LIFT + BACK IN MENUS'),
            ('CLICKY DIRECTIONS', 'ROLL/PITCH; RELEASE BRAKES'),
            ('CLICKY ENTER', 'FAN ON / OFF'),
            ('GROUND TILT', 'LOOK AROUND'),
            ('AIR: LEFT / RIGHT', 'ROLL LEFT / RIGHT'),
            ('AIR: FORWARD / BACK', 'NOSE DOWN / UP'),
            ('FLICK', 'COMPLETE ROLL / FRONT / BACK'),
            ('CENTER IN AIR', 'LEVELS AFTER FLIP FINISHES'))
    for i, (control, action) in enumerate(rows):
        y = 105 + i * 10
        _text(control, 10, y, MUTED)
        _text(action, 139, y, WHITE)
    _rect(8, 208, 304, 1, PANEL)
    _text('MIDDLE RIGHT: SCENES / CENTER: LEVEL', 10, 215, GOLD)
    _text('MENU OR INNER RIGHT: RETURN TO DRIVING', 10, 230, CYAN)
    return {'view': 'controls', 'physics_live': True}


def draw_scenes(scenes, selected, current):
    """Playable destinations; merely browsing never moves the car."""
    invalidate()
    screen.font = font.sins
    _rect(0, 0, WIDTH, HEIGHT, INK)
    _text('CHOOSE YOUR SCENE', 12, 12, WHITE)
    _text('PHYSICS STAYS LIVE', 205, 12, MUTED)
    row_step = min(56, 168 // len(scenes))
    for i, (slug, title, description) in enumerate(scenes):
        y = 32 + i * row_step
        active = i == selected
        _rect(8, y, 304, row_step-5, PANEL if active else INK)
        _rect(8, y, 3, row_step-5, CYAN if active else DIM)
        pen = CYAN if active else MUTED
        # A small open-curl diagram identifies the ramp.
        screen.pen = pen
        if slug == 'ramp':
            points = ((19, y+28), (17, y+22), (18, y+14), (24, y+8),
                      (32, y+7), (39, y+12), (42, y+20), (39, y+27),
                      (32, y+30), (24, y+30), (19, y+28))
            # The ramp has an exposed upper edge and an open approach.
            for j in range(5):
                screen.line(points[j][0], points[j][1], points[j+1][0], points[j+1][1])
        elif slug == 'collection':
            for j in range(4):
                _rect(17+j*6, y+17-j*2, 4, 12+j*2, pen)
        else:
            screen.line(18, y+31, 25, y+6)
            screen.line(39, y+31, 32, y+6)
            for j in range(3): _rect(28, y+10+j*7, 2, 4, pen)
        _text(title, 52, y+7, WHITE if active else MUTED)
        if slug == current: _text('HERE', 278, y+7, CYAN)
        _text(description.upper(), 52, y+22, MUTED)
    _text('DPAD OR MIDDLE RIGHT: BROWSE', 12, 208, GOLD)
    _text('OUTER: ENTER   INNER / MENU: RETURN', 12, 224, CYAN)
    return {'view': 'scenes', 'selected': scenes[selected][0], 'current': current}
