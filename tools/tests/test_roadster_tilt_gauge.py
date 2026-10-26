"""Host-only sensor-display and compact top-HUD checks, not native rendering.

The separate native suite verifies real glyph pixels and retained/full equality.
These checks guard missing/invalid input, disarmed sensor visibility, harmless
display-only updates and the bounded drawing work on noisy unchanged samples.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_roadster_hud_jets import physics


ROOT = Path(__file__).resolve().parents[2]
HUD = ROOT / 'apps/roadster_sandbox/hud.py'


class RecordingScreen:
    def __init__(self):
        self.calls = []
        self.pen = None

    def rectangle(self, *args):
        self.calls.append(('rect', args, self.pen))

    def text(self, *args):
        self.calls.append(('text', args, self.pen))


def load_hud():
    screen = RecordingScreen()
    namespace = {'screen': screen, 'NOZZLES': physics.NOZZLES,
                 'TILT_FULL_DEG': 28., 'font': SimpleNamespace(sins='sins'),
                 'color': SimpleNamespace(rgb=lambda *rgb: rgb)}
    nodes = [node for node in ast.parse(HUD.read_text()).body
             if not isinstance(node, (ast.Import, ast.ImportFrom))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(HUD), 'exec'), namespace)
    return namespace, screen


def motion(**changes):
    state = dict(calibrated=True, tilt_valid=True, armed=True,
                 angle_x=0., angle_y=0., tilt_x=0., tilt_y=0.,
                 flick_active=False)
    state.update(changes)
    return state


class TiltGaugeTests(unittest.TestCase):
    def setUp(self):
        self.hud, self.screen = load_hud()
        self.sim = SimpleNamespace(air_mode='unlimited', fan_force=0.,
                                   nozzle_forces=[0.] * 10, contact_count=4,
                                   chassis_contacts=0,
                                   basis=lambda: ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)))
        self.feedback = SimpleNamespace(speed_mph=0., rear_fraction=0.,
                                        rear_force=0., run_state='ready')

    def draw(self, sample=None, level=None, preserve=True):
        state = {} if sample is None else {'motion': sample, 'level': level or {}}
        return self.hud['draw'](self.sim, self.feedback, state=state, preserve=preserve)

    def test_disarmed_ground_sensor_is_visible_without_implying_delivered_thrust(self):
        sample = motion(armed=False, angle_x=-28., angle_y=16., tilt_x=-1., tilt_y=.5)
        before = sample.copy()
        shown = self.draw(sample)['tilt_gauge']
        self.assertEqual(shown['status'], 'SENSE')
        self.assertEqual(shown['percent'], (-100, 50))
        self.assertLess(shown['marker'][0], 0)
        self.assertGreater(shown['marker'][1], 0)
        self.assertEqual(sample, before)
        self.assertEqual(self.sim.nozzle_forces, [0.] * 10)
        self.assertEqual(shown['source'], 'sensor')

    def test_marker_reveals_small_sensed_angle_even_with_deadzone_command(self):
        centered = self.draw(motion())['tilt_gauge']
        slight = self.draw(motion(angle_x=3.5, angle_y=-3.5))['tilt_gauge']
        self.assertEqual(slight['percent'], (0, 0))
        self.assertNotEqual(slight['marker'], centered['marker'])
        self.assertEqual(slight['angles'], (3.5, -3.5))

    def test_ground_look_label_never_hides_invalid_sensor_or_arms_jets(self):
        for changes, expected in (({}, 'LOOK'), ({'calibrated': False}, 'CENTER'),
                                  ({'tilt_valid': False}, 'WAIT'),
                                  ({'sensor_clipped': True}, 'CLIP')):
            sample = motion(armed=False, **changes)
            shown = self.hud['draw'](self.sim, self.feedback,
                state={'motion': sample, 'ground_look': True}, preserve=True)['tilt_gauge']
            self.assertEqual(shown['status'], expected)
            self.assertFalse(shown['armed'])
            self.assertEqual(self.sim.nozzle_forces, [0.] * 10)

    def test_invalid_and_uncalibrated_samples_hide_stale_values(self):
        for sample, expected in ((motion(calibrated=False), 'CENTER'),
                                 (motion(tilt_valid=False), 'WAIT')):
            sample.update(angle_x=25., tilt_x=.9)
            shown = self.draw(sample)['tilt_gauge']
            self.assertEqual(shown['status'], expected)
            self.assertIsNone(shown['marker'])
            self.assertIsNone(shown['angles'])
            self.assertEqual(shown['percent'], (None, None))

    def test_feedback_labels_require_valid_armed_sensor_and_flick_takes_priority(self):
        level = {'level_state': 'level', 'level_strength': .52}
        shown = self.draw(motion(), level)['tilt_gauge']
        self.assertEqual(shown['status'], 'LEVEL')
        self.assertEqual(shown['level_strength'], 50)
        for sample, expected in ((motion(armed=False), 'SENSE'),
                                 (motion(tilt_valid=False), 'WAIT'),
                                 (motion(flick_active=True), 'FLICK')):
            self.assertEqual(self.draw(sample, level)['tilt_gauge']['status'], expected)
        for state, expected in (('steady', 'STEADY'), ('tilt', 'TILT'), ('off', 'READY')):
            self.assertEqual(self.draw(motion(), {'level_state': state})['tilt_gauge']['status'], expected)

    def test_clipped_regular_sample_is_explicit_and_never_displays_trusted_tilt(self):
        sample = motion(sensor_clipped=True, angle_x=19., tilt_x=.6, flick_active=True)
        shown = self.draw(sample, {'level_state': 'level', 'level_strength': 1.})['tilt_gauge']
        self.assertEqual(shown['status'], 'CLIP')
        self.assertTrue(shown['clipped'])
        self.assertFalse(shown['valid'])
        self.assertIsNone(shown['marker'])
        self.assertIsNone(shown['angles'])
        self.assertEqual(shown['percent'], (None, None))
        self.screen.calls.clear()
        recovered = self.draw(motion(angle_x=19., tilt_x=.6))['tilt_gauge']
        self.assertTrue(recovered['valid'])
        self.assertGreater(recovered['marker'][0], 0)
        self.assertTrue(self.screen.calls)

    def test_flick_only_label_explains_why_visible_lean_is_not_continuous_control(self):
        sample = motion(mode='flick', angle_x=18., tilt_x=.6)
        self.assertEqual(self.draw(sample, {'level_state': 'tilt'})['tilt_gauge']['status'],
                         'FLICK ONLY')
        sample['flick_active'] = True
        self.assertEqual(self.draw(sample)['tilt_gauge']['status'], 'FLICK')

    def test_invisible_sensor_noise_reuses_every_instrument_region(self):
        self.draw(motion(angle_x=12., tilt_x=.333))
        self.screen.calls.clear()
        shown = self.draw(motion(angle_x=12.01, tilt_x=.334))['tilt_gauge']
        self.assertEqual(shown['angles'], (12.01, 0.))
        self.assertEqual(len(self.screen.calls), 1)  # one top/world divider
        self.assertTrue(all(call[0] == 'rect' for call in self.screen.calls))

    def test_direction_change_draws_only_bounded_gauge_commands_above_world(self):
        self.draw(motion())
        self.screen.calls.clear()
        self.draw(motion(angle_x=80., angle_y=-80., tilt_x=1., tilt_y=-1.))
        self.assertLessEqual(len(self.screen.calls), 20)
        for kind, args, _pen in self.screen.calls:
            y = args[1] if kind == 'rect' else args[2]
            self.assertGreaterEqual(y, 0)
            self.assertLess(y, 50)
            if kind == 'rect':
                self.assertLessEqual(args[0] + args[2], 320)
                self.assertLessEqual(args[1] + args[3], 50)

    def test_removing_badge_sensor_clears_its_top_instrument_without_a_footer(self):
        desktop = self.draw()
        self.assertIsNone(desktop['tilt_gauge'])
        self.draw(motion())
        self.screen.calls.clear()
        restored = self.draw()
        self.assertIsNone(restored['tilt_gauge'])
        self.assertIn(('rect', (206, 0, 65, 50), self.hud['INK']), self.screen.calls)
        self.assertEqual(restored['hint_lines'], desktop['hint_lines'])
        self.assertEqual(restored['hint_lines'], ())
        self.assertTrue(all((args[1] if kind == 'rect' else args[2]) < 50
                            for kind, args, _pen in self.screen.calls))

    def test_live_instruments_never_paint_over_the_enlarged_world(self):
        self.feedback.speed_mph = 190.
        self.feedback.rear_fraction = 1.
        self.feedback.rear_force = 44000.
        self.sim.nozzle_forces = [nozzle[3] for nozzle in physics.NOZZLES]
        self.sim.fan_force = 18000.
        self.draw(motion(angle_x=-28., angle_y=28., tilt_x=-1., tilt_y=1.))
        self.assertEqual(self.hud['WORLD_RECT'], (0, 50, 320, 190))
        for kind, args, _pen in self.screen.calls:
            y = args[1] if kind == 'rect' else args[2]
            self.assertGreaterEqual(y, 0)
            if kind == 'rect':
                self.assertLessEqual(args[1] + args[3], 50)
            else:
                # The real sins font's visible glyph extends nine rows below y.
                self.assertLessEqual(args[2] + 10, 50)
        words = [args[0] for kind, args, _pen in self.screen.calls if kind == 'text']
        self.assertFalse(any('MENU' in word or 'AIR ' in word or '0-60' in word for word in words))


if __name__ == '__main__':
    unittest.main()
