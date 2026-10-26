"""Continuous Roadster physics sandbox, with an input-independent control API."""

from badgeware import *
from physics import Simulation, GROUND_Y
from tricks import TrickController
from speed_feedback import SpeedFeedback
from stunt_feedback import StuntFeedback
from world import PARKED, find_vehicle, nearest_vehicle, lineup_start
from badge_motion import BadgeMotion
from ground_camera import GroundCamera
from badge_controls import (mix_controls, prepare_motion, NO_MOTION, RotationControl,
                            held_lift_common, LIFT_CEILING)

badge.mode(HIRES | VSYNC)
screen.font = font.sins

import scene
import hud
WORLD_VIEWPORT = rect(*hud.WORLD_RECT)
# The world clears its viewport and the HUD redraws changed regions. Disable
# the framework's full-frame clear so unchanged instruments survive each poll.
original_clear = badge.default_clear
badge.default_clear = None

scene_mode = "collection"
sim = Simulation("flat", obstacles=PARKED)
lineup_start(sim)
trick = TrickController()
feedback = SpeedFeedback()
feedback.reset(sim)
stunt = StuntFeedback()
stunt.reset(sim)
hud_state = {}
reduced_effects = False
frame_count = 0
controls = {"throttle": 0.0, "brake": 0.0, "steer": 0.0,
            "fan": 0.0, "nozzles": [0.0] * 10}
external_controls_active = False
physical_fan = False
motion_input = BadgeMotion()
rotation_control = RotationControl()
ground_camera = GroundCamera()
physical_inputs = ((), None, 0., ())
physical_motion_source = None
physical_motion_signal = NO_MOTION
physical_level_armed = False
motion_allowed = False
motion_neutral_required = True
motion_contact_disarm = False
last_flick_count = 0
badge_active = False
menu_open = False
menu_index = 0
help_open = False
scenes_open = False
scene_index = 0
release_pads = False
last_driving = controls
touch_buttons = ()
clicky_buttons = ()
exited = False
# In the inverted gamepad grip the firmware names the outer right pad BACK.
# Give the player their requested outer=Select, inner=Back app convention.
PAD_SELECT = BUTTON_BACK
PAD_BACK = BUTTON_SELECT
BUTTONS = (('LEFT', BUTTON_LEFT), ('RIGHT', BUTTON_RIGHT),
           ('UP', BUTTON_UP), ('DOWN', BUTTON_DOWN),
           ('SELECT', PAD_SELECT), ('BACK', PAD_BACK))
CLICKY_BUTTONS = (('LEFT', BUTTON_LEFT), ('RIGHT', BUTTON_RIGHT),
                 ('UP', BUTTON_UP), ('DOWN', BUTTON_DOWN),
                 ('SELECT', BUTTON_SELECT))
MENU_ITEMS = ('Reset car', 'Fan', 'Refill air', 'Center grip', 'Scene', 'Camera', 'Motion', 'Air supply', 'Controls')
SCENES = (('flat', 'AIRFIELD', 'Open space to drive, fly and practice'),
          ('ramp', 'OPEN RAMP', 'Climb the open curl and launch'),
          ('collection', 'TESLA LINEUP', 'Jump over the entire Tesla collection'))
notice = ""
notice_until = 0.0
INK = color.rgb(14, 21, 38)
WHITE = color.rgb(240, 247, 250)
MUTED = color.rgb(143, 166, 186)
CYAN = color.rgb(67, 227, 213)
GOLD = color.rgb(239, 201, 116)


def set_controls(values):
    """Supply normalised controls from any controller; omitted values release.

    The playground adapter calls this same public API. The deployable app has
    no simulator imports; a future touch/IMU controller can call it unchanged.
    """
    global external_controls_active
    for key in ("throttle", "brake", "steer", "fan"):
        controls[key] = values.get(key, 0.0)
    supplied = values.get("nozzles", ())
    for i in range(10):
        controls["nozzles"][i] = supplied[i] if i < len(supplied) else 0.0
    external_controls_active = bool(controls['throttle'] or controls['brake'] or
        controls['steer'] or controls['fan'] or any(controls['nozzles']))


def reset_sim(scene_name=None):
    global physical_fan, notice, notice_until, scene_mode, release_pads
    global motion_allowed, motion_neutral_required, motion_contact_disarm
    if scene_name is not None:
        scene_mode = scene_name
        scene.follow_view = "behind" if scene_name == "ramp" else "chase"
    sim.set_obstacles(PARKED if scene_mode == "collection" else ())
    sim.reset("flat" if scene_mode == "collection" else scene_mode)
    if scene_mode == "collection":
        lineup_start(sim)
    trick.reset()
    rotation_control.reset()
    reset_ground_camera()
    feedback.reset(sim)
    stunt.reset(sim)
    physical_fan = False
    motion_allowed = False
    motion_neutral_required = True
    motion_contact_disarm = False
    release_pads = True
    set_controls({})
    scene.camera_yaw = 0.0
    scene.camera_time = -1.0
    scene.collection_enabled = scene_mode == "collection"
    scene.inspect_target = None
    scene.camera_view = scene.follow_view
    notice = "Reset: car and air restored. Release held keys, then drive."
    notice_until = sim.time + 3.0


def reset_ground_camera():
    ground_camera.reset()
    scene.ground_look_yaw = scene.ground_look_height = 0.


def control_action(action, value=None):
    """Explicit controller actions, separate from continuously held forces."""
    global physical_fan, notice, notice_until, reduced_effects, release_pads
    global help_open, menu_open, scenes_open, scene_index
    global motion_allowed, motion_neutral_required
    if action == "reset":
        reset_sim()
    elif action == "scene":
        if value not in ("collection", "ramp", "flat"):
            raise ValueError("scene must be collection, ramp or flat")
        reset_sim(value)
    elif action == "visit":
        item = find_vehicle(value)
        reset_sim("collection")
        sim.x = item["x"]
        sim.z = item["z"] - item["length"] / 2 - 9.
        sim.q = (1., 0., 0., 0.)
        feedback.reset(sim)
        stunt.reset(sim)
        notice = "Approaching " + item["name"] + ". Drive, lift or jump."
        notice_until = sim.time + 5.
    elif action == "inspect":
        if value != "roadster":
            find_vehicle(value)  # Validate before changing the active scene.
        if scene_mode != "collection":
            reset_sim("collection")
        trick.cancel()
        set_controls({"fan": controls["fan"]})
        scene.inspect_target = None if value == "roadster" else value
        scene.camera_view = "orbit"
        reset_ground_camera()
    elif action == "camera":
        if value not in ("chase", "behind", "orbit", "front", "side", "rear"):
            raise ValueError("unknown camera view")
        reset_ground_camera()
        scene.camera_view = value
        if value in ("chase", "behind"):
            scene.follow_view = value
            scene.inspect_target = None
    elif action == "effects":
        if value not in ("full", "reduced"):
            raise ValueError("effects must be full or reduced")
        reduced_effects = value == "reduced"
    elif action == "refill":
        trick.cancel()
        sim.refill_air()
        notice = "Air refilled in place; position and motion preserved."
        notice_until = sim.time + 3.0
    elif action == "air":
        sim.set_air_mode(value)
        notice = ('Unlimited air: full-strength jets for practice.' if value == 'unlimited'
                  else 'Limited air: pressure falls as the tank is used.')
        notice_until = sim.time + 3.0
    elif action == "trick":
        trick.start(value, sim)
        notice = trick.message
        notice_until = sim.time + 4.0
    elif action == "cancel_trick":
        trick.cancel()
        notice = "Trick cancelled; manual controls remain active."
        notice_until = sim.time + 3.0
    elif action == "badge":
        activate_badge()
    elif action == "help":
        if trick.committed:
            trick.cancel()
        rotation_control.reset()
        activate_badge()
        help_open = value is not False
        menu_open = False
        scenes_open = False
        release_pads = True
    elif action == "scenes":
        if trick.committed:
            trick.cancel()
        activate_badge()
        scenes_open = value is not False
        scene_index = next(i for i in range(len(SCENES)) if SCENES[i][0] == scene_mode)
        menu_open = help_open = False
        rotation_control.reset()
        release_pads = True
    elif action == "center":
        if trick.committed:
            trick.cancel()
        rotation_control.reset()
        reset_ground_camera()
        activate_badge()
        motion_input.center()
        release_pads = True
    elif action == "motion":
        if value not in ('flick', 'tilt', 'both'):
            raise ValueError('motion must be flick, tilt or both')
        if trick.committed:
            trick.cancel()
        activate_badge()
        motion_input.set_mode(value)
        motion_allowed = False
        motion_neutral_required = True
    elif action == "quality":
        scene.set_quality(value)
    else:
        raise ValueError("unknown control action")
    return control_status()


def control_status():
    """Small controller-facing telemetry; no browser or hardware dependency."""
    lift = sim.lift_status()
    message = lift["message"]
    if trick.active:
        message = trick.message
    elif sim.time < notice_until:
        message = notice
    # A held lift command should explain a lack of takeoff immediately.
    if not trick.active and (any(controls["nozzles"][4:8]) or
                            any(last_driving["nozzles"][4:8])) and lift["reason"] != "ready":
        message = lift_message(lift)
    nearby = nearest_vehicle(sim.x, sim.z) if scene_mode == "collection" else None
    inspecting = scene.inspect_target or ("roadster" if scene.camera_view not in ("chase", "behind") else "")
    if inspecting:
        name = "Roadster" if inspecting == "roadster" else find_vehicle(inspecting)["name"]
        message = "Viewing " + name + ". Driving controls return to the Roadster."
    return {"message": message, "trick": trick.active, "phase": trick.phase,
            "committed_flip": trick.committed,
            "lift_ratio": lift["ratio"], "air_percent": sim.air_fraction * 100,
            "air_mode": sim.air_mode,
            "fan_on": bool(controls["fan"] or physical_fan),
            "lift_reason": lift["reason"], "scene": scene_mode,
            "parked_count": len(PARKED) if scene_mode == "collection" else 0,
            "nearest_vehicle": nearby["name"] if nearby else "",
            "inspecting": inspecting, "camera": scene.camera_view,
            "effects": "reduced" if reduced_effects else "full",
            "speed_mph": feedback.speed_mph, "rear_force": feedback.rear_force,
            "boost_mix": feedback.boost_mix, "speed_intensity": feedback.intensity,
            "acceleration_state": feedback.run_state,
            "acceleration_time": feedback.run_time,
            "acceleration_reason": feedback.run_reason,
            "badge_active": badge_active, "motion_phase": motion_input.phase,
            "ground_look": ground_camera.active,
            "motion_mode": motion_input.model.get('mode', 'both'),
            "motion_allowed": motion_allowed,
            "motion_neutral_required": motion_neutral_required,
            "menu_open": menu_open, "help_open": help_open,
            "scenes_open": scenes_open, "scene_index": scene_index,
            "quality": scene.quality, "flight": stunt.snapshot()}


def activate_badge():
    """A controller explicitly selects the portable badge display/input setup."""
    global badge_active
    if not badge_active:
        badge_active = True
        scene.set_quality('badge')
        sim.set_step_hz(40)
        # Poll and draw after at most 100 ms of simulated work. Under heavy
        # collision load, discard excess elapsed time instead of compounding
        # a slow frame with eight catch-up ticks. Individual physics steps
        # use 40 Hz with 240 Hz hard-impact refinement; overload still records
        # discarded elapsed time instead of hiding simulation slowdown.
        sim.max_substeps = 4
        sim.set_air_mode('unlimited')
        motion_input.start()


def badge_snapshot():
    return {'active': badge_active, 'motion': motion_input.snapshot(),
            'camera_look': {'active': ground_camera.active,
                'yaw': ground_camera.yaw, 'height': ground_camera.height},
            'menu_open': menu_open, 'menu_index': menu_index, 'help_open': help_open,
            'scenes_open': scenes_open, 'scene_index': scene_index, 'scene': scene_mode,
            'touch_buttons': touch_buttons, 'clicky_buttons': clicky_buttons,
            'rotation': rotation_control.snapshot(),
            'trick': trick.active, 'trick_phase': trick.phase,
            'committed_flip': trick.committed, 'trick_rotation': trick.rotation,
            'motion_allowed': motion_allowed,
            'motion_neutral_required': motion_neutral_required,
            'release_pads': release_pads, 'driving': last_driving,
            'frame_count': frame_count, 'exited': exited,
            'quality': scene.quality, 'render_cores': scene.render_cores,
            'physics_hz': 1. / sim.fixed_dt,
            'impact_refinements': sim.total_refinements,
            'air_mode': sim.air_mode, 'lift_status': sim.lift_status(),
            'flight': stunt.snapshot(),
            'hud_hint': badge_hint(),
            'sim_time': sim.time, 'dropped_time': sim.dropped_time}


def lift_message(lift):
    if badge_active:
        return {'low_pressure': 'Low pressure: MENU > Refill air (outer pad confirms)',
                'fan': 'Fan suction holds the car: clicky Enter switches it off',
                'orientation': 'Lift follows the car: it points sideways or down'}.get(
                    lift['reason'], lift['message'])
    return lift['message']


def badge_hint():
    if release_pads:
        return 'RELEASE RIGHT PADS AND CLICKY BUTTONS'
    if trick.active:
        return trick.active.upper() + ' / ' + trick.phase.upper()
    lift = sim.lift_status()
    if lift['reason'] == 'low_pressure':
        return 'LOW PRESSURE / MENU > REFILL AIR'
    if 'BACK' in touch_buttons or any(controls['nozzles'][4:8]):
        if lift['reason'] == 'fan':
            return 'FAN HOLDS CAR / CLICKY ENTER: FAN OFF'
        if lift['reason'] == 'orientation':
            return 'LIFT POINTS WITH CAR / ROLL TO RIGHT IT'
        if 'BACK' in touch_buttons and sim.y-GROUND_Y >= LIFT_CEILING*.5:
            return 'SMOOTH LIFT / RELEASE TO DESCEND'
    if motion_input.phase == 'ready' and motion_neutral_required:
        return 'WAITING FOR FRESH MOTION SAMPLE'
    if (motion_input.phase == 'ready' and sim.airborne and
            not motion_input.model.get('tilt_valid', False)):
        return 'MOTION SETTLING / HOLD A STEADY LEAN'
    if motion_input.model.get('armed'):
        if rotation_control.level_state in ('level', 'steady'):
            return ('CENTER: LEVELING WITH JETS' if rotation_control.level_state == 'level'
                    else 'CENTER: LEVEL / TILT TO ROLL OR FLIP')
        mode = motion_input.model.get('mode', 'both')
        hint = 'MOTION LIVE / ' + ('TILT + FLICK' if mode == 'both' else mode.upper())
        if motion_input.model.get('flick_pending'):
            hint += ' / READING STROKE'
        return hint
    if motion_input.phase == 'ready':
        return 'MIDDLE RIGHT: SCENES / LIFT OFF THEN TILT'
    return 'OUTER: BOOST  INNER: LIFT  MENU: OPTIONS'


def menu_choice():
    global physical_fan, menu_open, release_pads
    choice = MENU_ITEMS[menu_index]
    if choice == 'Fan':
        physical_fan = not physical_fan
    elif choice == 'Reset car':
        reset_sim()
    elif choice == 'Refill air':
        control_action('refill')
    elif choice == 'Center grip':
        reset_ground_camera()
        motion_input.center()
    elif choice == 'Scene':
        control_action('scenes')
    elif choice == 'Camera':
        control_action('camera', 'behind' if scene.follow_view == 'chase' else 'chase')
    elif choice == 'Motion':
        choices = ('flick', 'tilt', 'both')
        mode = motion_input.model.get('mode', 'flick')
        motion_input.set_mode(choices[(choices.index(mode) + 1) % len(choices)])
    elif choice == 'Air supply':
        control_action('air', 'unlimited' if sim.air_mode == 'limited' else 'limited')
    elif choice == 'Controls':
        control_action('help')
    menu_open = False
    release_pads = True


def draw_badge_overlay():
    if help_open or menu_open or scenes_open:
        # These panels overlap the retained instrument bars. Repaint those
        # bars next frame, including the first frame after a panel closes.
        hud.invalidate()
    if scenes_open:
        hud.draw_scenes(SCENES, scene_index, scene_mode)
    elif help_open:
        hud.draw_help()
    elif menu_open:
        screen.pen = INK
        screen.rectangle(39, 39, 242, 171)
        label('ROADSTER OPTIONS %d/%d' % (menu_index + 1, len(MENU_ITEMS)), 51, 49, CYAN)
        first = max(0, menu_index - 7)
        for i in range(first, min(first + 8, len(MENU_ITEMS))):
            name = MENU_ITEMS[i]
            value = ''
            if name == 'Fan': value = ' ON' if physical_fan else ' OFF'
            elif name == 'Scene': value = ' > CHOOSE'
            elif name == 'Camera': value = ' ' + scene.follow_view.upper()
            elif name == 'Motion': value = ' ' + motion_input.model.get('mode', 'flick').upper()
            elif name == 'Air supply': value = ' ' + sim.air_mode.upper()
            label(('> ' if i == menu_index else '  ') + name.upper() + value,
                  51, 66 + (i-first) * 15, GOLD if i == menu_index else WHITE)
        label('UP/DN CHOOSE  SELECT OK  INNER CLOSE', 43, 193, MUTED)
    elif motion_input.phase in ('center', 'calibrating', 'error'):
        screen.pen = INK
        screen.rectangle(7, 53, 306, 36)
        if motion_input.phase == 'center':
            label('HOLD YOUR PLAYING GRIP. TAP OUTER RIGHT.', 13, 59, GOLD)
            label('THIS CENTERS MOTION; DPAD ALREADY DRIVES.', 13, 74, MUTED)
        elif motion_input.phase == 'calibrating':
            label('HOLD STILL: CENTERING %d%%' % int(
                motion_input.model.get('calibration_progress', 0) * 100), 13, 59, GOLD)
            label('RELEASE RIGHT PADS AND CLICKY BUTTONS.', 13, 74, MUTED)
        else:
            label('MOTION UNAVAILABLE / BUTTONS STILL WORK', 13, 59, GOLD)
            label(motion_input.error[:48], 13, 74, MUTED)


def label(text, x, y, pen=WHITE):
    screen.pen = pen
    screen.text(text, x, y)


_level_view = {'level_state': 'off', 'level_strength': 0.}


def draw_hud():
    global hud_state
    if badge_active:
        # Only the sensor gauge uses this small view; no full rotation snapshot.
        _level_view['level_state'] = rotation_control.level_state
        _level_view['level_strength'] = rotation_control.level_strength
    hud_state = hud.draw(sim, feedback, stunt.snapshot(), {
        'fan_on': bool(controls['fan'] or physical_fan),
        'badge_active': badge_active,
        'motion': motion_input.model if badge_active else None,
        'ground_look': ground_camera.active,
        'level': _level_view if badge_active else None,
        'committed_flip': trick.committed,
        'inspecting_name': find_vehicle(scene.inspect_target)['name'] if scene.inspect_target else '',
        'scene': scene_mode, 'camera': scene.camera_view}, True)


def observe_physics(dt, simulation, values):
    feedback.update(dt, simulation, values)
    stunt.update(dt, simulation, values)


def physical_driving(dt, simulation):
    global notice, notice_until
    global physical_motion_source, physical_motion_signal, motion_contact_disarm
    global physical_level_armed
    buttons, model, fan, manual = physical_inputs
    # update() publishes a fresh immutable input tuple and sensor snapshot per
    # poll. Keep those axes across catch-up ticks; rates and jet duties remain
    # live on every tick. Replacing the tuple also invalidates this cache.
    if physical_inputs is not physical_motion_source:
        physical_motion_signal = prepare_motion(model)
        # Read sensor/context validity once per poll. The controller checks
        # ground contact and current attitude again on every physics tick.
        physical_level_armed = bool(model and model.get('calibrated') and
            model.get('armed') and model.get('tilt_valid') and
            not model.get('sensor_clipped') and not model.get('flick_pending') and
            not any(controls['nozzles']) and not manual and not trick.active)
        physical_motion_source = physical_inputs
    # Ground/roof contact can return inside a catch-up frame. Stop automatic
    # air controls at that tick, even though the sensor snapshot is unchanged.
    if (motion_allowed and not simulation.airborne and
            'SELECT' not in buttons and 'BACK' not in buttons):
        # A rebound in this same catch-up frame must not replay the old flick.
        # Discard this poll and give the recognizer a disarmed sample next poll.
        physical_motion_signal = NO_MOTION
        motion_contact_disarm = True
    signal = physical_motion_signal if motion_allowed else NO_MOTION
    if trick.active and any(name in manual for name in ('LEFT', 'RIGHT', 'UP', 'DOWN')):
        trick.cancel()
        notice, notice_until = trick.message, simulation.time + 3.
    if trick.active:
        # A recognized flick owns the jets until its full rotation and braking
        # finish. Holding the lift/boost pad used to arm it is not cancellation.
        # Clicky directions and explicit laptop jets still override below.
        preset_buttons = (tuple(name for name in buttons if name not in ('SELECT', 'BACK'))
                          if trick.committed else buttons)
        physical = mix_controls(preset_buttons, fan=fan, speed=simulation.speed)
    else:
        physical = rotation_control.mix(dt, simulation, buttons, model, fan, manual,
            motion_signal=signal,
            level_enabled=physical_level_armed and motion_allowed and not motion_contact_disarm,
            lift_common=held_lift_common(simulation) if 'BACK' in buttons else 1.)
    if not external_controls_active:
        return physical
    return {'throttle': physical['throttle'] or controls['throttle'],
            'steer': physical['steer'] or controls['steer'],
            'brake': max(physical['brake'], controls['brake']),
            'fan': max(physical['fan'], controls['fan']),
            'nozzles': [max(a, b) for a, b in zip(physical['nozzles'], controls['nozzles'])]}


def filter_driving(dt, simulation, values):
    global last_driving
    driving = physical_driving(dt, simulation)
    last_driving = trick.update(dt, simulation, driving) if trick.active else driving
    return last_driving


def update():
    global frame_count, physical_fan, notice, notice_until
    global menu_open, menu_index, release_pads, last_driving
    global help_open, scenes_open, scene_index, touch_buttons, clicky_buttons
    global physical_inputs
    global motion_allowed, motion_neutral_required, motion_contact_disarm
    global last_flick_count
    frame_count += 1
    # Plain loops over the native held() calls: no per-poll generator objects.
    held = badge.pad.held
    names = []
    for name, button in BUTTONS:
        if held(button):
            names.append(name)
    touch_buttons = tuple(names)
    held = badge.buttons.held
    names = []
    for name, button in CLICKY_BUTTONS:
        if held(button):
            names.append(name)
    clicky_buttons = tuple(names)
    buttons, manual = touch_buttons, clicky_buttons
    menu_pressed = badge.pressed(BUTTON_MENU)
    scenes_pressed = badge.pad.pressed(BUTTON_HOME)
    confirm_pressed = badge.pad.pressed(PAD_SELECT) or badge.buttons.pressed(BUTTON_SELECT)
    cancel_pressed = badge.pad.pressed(PAD_BACK)
    overlay_input = menu_open or help_open or scenes_open or menu_pressed or scenes_pressed
    if buttons or manual or menu_pressed or scenes_pressed:
        activate_badge()
    if scenes_pressed:
        if scenes_open:
            scene_index = (scene_index + 1) % len(SCENES)
        else:
            control_action('scenes')
        release_pads = True
    elif scenes_open:
        if menu_pressed or cancel_pressed:
            scenes_open = False
            release_pads = True
        elif confirm_pressed:
            reset_sim(SCENES[scene_index][0])
            scenes_open = False
        elif badge.pressed(BUTTON_UP) or badge.pressed(BUTTON_LEFT):
            scene_index = (scene_index - 1) % len(SCENES)
        elif badge.pressed(BUTTON_DOWN) or badge.pressed(BUTTON_RIGHT):
            scene_index = (scene_index + 1) % len(SCENES)
    elif help_open:
        if menu_pressed or confirm_pressed or cancel_pressed:
            help_open = False
            release_pads = True
    elif menu_pressed:
        menu_open = not menu_open
        if menu_open:
            menu_index = 0
        release_pads = True
    # Opening the menu consumes this poll's edges. A simultaneously pressed
    # boost/lift/clicky must not confirm Reset or Fan before its row is visible.
    # Held inputs do not become a new edge; release and press again to choose.
    if menu_open and not scenes_open and not menu_pressed:
        if badge.pressed(BUTTON_UP): menu_index = (menu_index - 1) % len(MENU_ITEMS)
        if badge.pressed(BUTTON_DOWN): menu_index = (menu_index + 1) % len(MENU_ITEMS)
        if confirm_pressed: menu_choice()
        if cancel_pressed:
            menu_open = False
            release_pads = True
    elif not overlay_input and motion_input.phase == 'center' and badge.pad.pressed(PAD_SELECT):
        reset_ground_camera()
        motion_input.center()
        release_pads = True
    if (motion_input.phase != 'calibrating' and
            'SELECT' not in touch_buttons and 'BACK' not in touch_buttons and
            not clicky_buttons):
        release_pads = False
    if overlay_input or menu_open or help_open or scenes_open:
        buttons, manual = (), ()
    if release_pads or motion_input.phase == 'calibrating':
        buttons = tuple(name for name in buttons if name not in ('SELECT', 'BACK'))
        manual = ()
    elif not overlay_input and not help_open and not scenes_open and badge.buttons.pressed(BUTTON_SELECT):
        physical_fan = not physical_fan
    dt = 0 if frame_count == 1 else badge.ticks_delta
    external_nozzles = any(controls['nozzles'])
    if trick.committed and (overlay_input or menu_open or help_open or scenes_open or
            release_pads or motion_input.phase != 'ready'):
        trick.cancel()
    motion_blocked = (not badge_active or overlay_input or menu_open or help_open or scenes_open or
        release_pads or motion_input.phase != 'ready' or trick.active or external_nozzles)
    if motion_blocked:
        motion_neutral_required = True
    motion_allowed = bool(not motion_blocked and not motion_neutral_required and
        not motion_contact_disarm and
        ('SELECT' in buttons or 'BACK' in buttons or sim.airborne))
    model = motion_input.update(dt, motion_allowed)
    motion_contact_disarm = False
    # Commit one complete physical flip per recognized stroke, not once per
    # frame of its decaying sensor pulse. Consume blocked events as well so
    # closing a menu, changing modes or landing cannot replay an old gesture.
    flick_count = model.get('flick_count', 0)
    fresh_flick = flick_count > last_flick_count
    last_flick_count = flick_count
    if (fresh_flick and motion_allowed and not trick.active and
            not any(name in manual for name in ('LEFT', 'RIGHT', 'UP', 'DOWN')) and
            model.get('mode') in ('both', 'flick') and model.get('armed') and
            model.get('calibrated') and model.get('tilt_valid') and
            not model.get('sensor_clipped')):
        flick = model.get('last_flick', {})
        x, y = flick.get('x', 0.), flick.get('y', 0.)
        if x or y:
            direction = (('right' if x > 0. else 'left') if abs(x) >= abs(y)
                         else ('back' if y > 0. else 'forward'))
            if trick.start_flick(direction, sim):
                rotation_control.reset()
                motion_allowed = False
                motion_neutral_required = True
            notice, notice_until = trick.message, sim.time + 3.
    # Require a fresh valid sample after an overlay, preset or recenter, not
    # an exact neutral grip. Current tilt resumes on the next poll. The sensor
    # clears gesture history while disarmed and requires new quiet evidence
    # before recognizing another flick, so no stored burst can replay.
    # Keep the historical telemetry name for existing controller clients.
    if (not motion_blocked and not trick.active and motion_neutral_required and model.get('calibrated') and
            model.get('tilt_valid', False) and not model.get('sensor_clipped', False)):
        motion_neutral_required = False
    if (overlay_input or menu_open or help_open or scenes_open or release_pads or
            motion_input.phase == 'calibrating' or trick.active or
            (external_nozzles and not manual)):
        rotation_control.reset()
    physical_inputs = (buttons, model, int(physical_fan), manual)
    driving = physical_driving(0., sim)
    last_driving = driving
    if (driving["throttle"] or driving["brake"] or driving["steer"] or
            any(driving["nozzles"]) or trick.active):
        scene.inspect_target = None
        scene.camera_view = scene.follow_view
    previous_trick = trick.active
    sim.step(dt / 1000.0, driving,
             # Tilt rates and centered upright recovery use actual jets;
             # feedback must see each completed fixed physics tick.
             control_filter=filter_driving if (rotation_control.needs_feedback(
                 physical_motion_signal if motion_allowed else NO_MOTION,
                 level_enabled=physical_level_armed and motion_allowed and
                 not motion_contact_disarm) or trick.active or 'BACK' in buttons) else None,
             observer=observe_physics)
    if previous_trick and not trick.active:
        notice = trick.message or "Trick finished; keep driving."
        notice_until = sim.time + 3.0
    # Read contact after the completed physics ticks: a launch immediately
    # hands tilt back to the jets, while stable wheel contact enables looking.
    # Chassis contact alone includes wall strikes and an upside-down car.
    q = sim.q
    ground_camera.update(dt / 1000., model,
        badge_active and motion_input.phase == 'ready' and not sim.airborne and
        sim.contact_count >= 2 and 1.-2.*(q[1]*q[1]+q[3]*q[3]) > .45 and
        not (overlay_input or menu_open or help_open or scenes_open or release_pads or
             trick.active or external_nozzles or scene.inspect_target) and
        scene.camera_view in ('chase', 'behind') and
        'SELECT' not in buttons and 'BACK' not in buttons and
        'LEFT' not in manual and 'RIGHT' not in manual and
        'UP' not in manual and 'DOWN' not in manual)
    scene.ground_look_yaw = ground_camera.yaw
    scene.ground_look_height = ground_camera.height
    scene.draw(sim, feedback, reduced_effects, viewport=WORLD_VIEWPORT)
    draw_hud()
    if badge_active:
        draw_badge_overlay()


def on_exit():
    global exited
    if exited:
        return
    try:
        motion_input.close()
    finally:
        try:
            scene.restore_renderer()
        finally:
            badge.default_clear = original_clear
    exited = True


run(update)
