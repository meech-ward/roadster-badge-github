"""Read-only acceleration telemetry against real portable sandbox physics."""
import importlib.util
import math
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[2] / "apps/roadster_sandbox"
SPEC = importlib.util.spec_from_file_location("speed_test_physics", APP / "physics.py")
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
SPEC = importlib.util.spec_from_file_location("speed_test_feedback", APP / "speed_feedback.py")
f = importlib.util.module_from_spec(SPEC)
old_physics = sys.modules.get("physics")
sys.modules["physics"] = p
try:
    SPEC.loader.exec_module(f)
finally:
    if old_physics is None:
        del sys.modules["physics"]
    else:
        sys.modules["physics"] = old_physics


def driving(boost=False, fan=0):
    return {"throttle": 1., "fan": fan,
            "nozzles": [float(boost)]*4+[0.]*6}


def measured(boost=False, schedule=(p.FIXED_DT,), seconds=3., fan=0):
    sim, feedback = p.Simulation("flat"), f.SpeedFeedback()
    feedback.reset(sim)
    index = 0
    while sim.time < seconds-1.e-8:
        dt = min(schedule[index % len(schedule)], seconds-sim.time-sim._accumulator)
        sim.step(dt, driving(boost, fan), observer=feedback.update)
        index += 1
    return sim, feedback


class SpeedFeedbackTests(unittest.TestCase):
    def test_motor_and_boost_times_are_interpolated_physical_measurements(self):
        for boost in (False, True):
            with self.subTest(boost=boost):
                sim, feedback = p.Simulation('flat'), f.SpeedFeedback()
                feedback.reset(sim)
                previous = (sim.time, sim.speed)
                crossing = None
                target = 60.*.44704  # Exact mph-to-m/s conversion.
                for _ in range(6*120):
                    sim.step(p.FIXED_DT, driving(boost), observer=feedback.update)
                    sample = (sim.time, sim.speed)
                    if previous[1] < target <= sample[1]:
                        crossing = (previous, sample)
                        break
                    previous = sample
                self.assertIsNotNone(crossing, 'The real ground launch never reached 60 mph')
                (t0, v0), (t1, v1) = crossing
                expected = t0+(target-v0)*(t1-t0)/(v1-v0)
                self.assertEqual(feedback.run_state, "complete")
                self.assertAlmostEqual(feedback.run_time, expected, places=9)
                self.assertGreater(feedback.run_time, t0)
                self.assertLess(feedback.run_time, t1)
                self.assertNotEqual(feedback.run_time % p.FIXED_DT, 0.)
                self.assertGreater(feedback.forward_mph, 60.)
                self.assertEqual(feedback.run_reason, "Measured 0-60 mph")

    def test_boosted_launch_is_subsecond_at_all_rates_and_air_modes(self):
        for hz in (40, 60, 120):
            for air in ('limited', 'unlimited'):
                with self.subTest(hz=hz, air=air):
                    sim, feedback = p.Simulation('flat'), f.SpeedFeedback()
                    sim.set_step_hz(hz)
                    sim.set_air_mode(air)
                    feedback.reset(sim)
                    for _ in range(hz):
                        sim.step(sim.fixed_dt, driving(True), observer=feedback.update)
                        self.assertFalse(sim.airborne)
                        self.assertEqual(sim.chassis_contacts, 0)
                        if feedback.run_state == 'complete':
                            break
                    self.assertEqual(feedback.run_state, 'complete')
                    self.assertGreater(feedback.run_time, .5)
                    self.assertLess(feedback.run_time, 1.)

    def test_timer_and_smoothing_are_independent_of_render_schedule(self):
        _, reference = measured(True)
        fields = ("run_time", "speed_mph", "rear_force", "rear_fraction",
                  "boost_mix", "speed_mix", "intensity")
        for schedule in ((1./15,), (1./30,), (1./60,), (.016, .029, .008, .041)):
            with self.subTest(schedule=schedule):
                _, actual = measured(True, schedule)
                self.assertEqual(actual.run_state, reference.run_state)
                for field in fields:
                    self.assertAlmostEqual(getattr(actual, field), getattr(reference, field), places=9)

    def test_observer_does_not_change_physics_or_reserves(self):
        plain, observed = p.Simulation("flat"), p.Simulation("flat")
        feedback = f.SpeedFeedback()
        feedback.reset(observed)
        for frame in range(90):
            controls = driving(frame < 30, fan=.2)
            controls["steer"] = .15 if frame > 50 else 0.
            plain.step(1./30, controls)
            observed.step(1./30, controls, observer=feedback.update)
        self.assertEqual(plain.__dict__, observed.__dict__)

    def test_fan_does_not_disable_boost_feedback_or_timer(self):
        # Observe the launch while the finite reservoir can still supply it;
        # a held command must not keep the cue lit after the gas runs out.
        sim, feedback = measured(True, seconds=1., fan=1)
        self.assertGreater(sim.fan_force, 1000.)
        self.assertGreater(feedback.rear_force, 1000.)
        self.assertGreater(feedback.intensity, .2)
        self.assertEqual(feedback.run_state, "complete")
        self.assertGreater(feedback.run_time, 0.)

    def test_empty_tank_cannot_light_boost_from_held_command(self):
        sim, feedback = p.Simulation("flat"), f.SpeedFeedback()
        sim.air_mass = p.TANK_RESIDUAL
        sim.mass = p.DRY_MASS+sim.air_mass
        feedback.reset(sim)
        for _ in range(60):
            sim.step(p.FIXED_DT, {"nozzles": [1.]*4+[0.]*6}, observer=feedback.update)
        self.assertEqual(feedback.rear_force, 0.)
        self.assertEqual(feedback.rear_fraction, 0.)
        self.assertEqual(feedback.boost_mix, 0.)
        self.assertEqual(feedback.intensity, 0.)
        self.assertEqual(feedback.run_state, "ready")

    def test_cue_tracks_actual_partial_force_and_decays_when_released(self):
        sim, feedback = p.Simulation("flat"), f.SpeedFeedback()
        feedback.reset(sim)
        sim.step(p.FIXED_DT, {"nozzles": [.5]+[0.]*9}, observer=feedback.update)
        self.assertAlmostEqual(feedback.rear_force, p.NOZZLES[0][3] * .5)
        self.assertAlmostEqual(feedback.rear_fraction, .125)
        self.assertGreater(feedback.boost_mix, 0.)
        previous = feedback.boost_mix
        for _ in range(120):
            sim.step(p.FIXED_DT, {}, observer=feedback.update)
        self.assertEqual(feedback.rear_force, 0.)
        self.assertLess(feedback.boost_mix, previous*.001)

    def test_lift_invalidates_ground_run_and_cannot_supply_speed_effect(self):
        sim, feedback = p.Simulation("flat"), f.SpeedFeedback()
        feedback.reset(sim)
        sim.step(.1, driving(), observer=feedback.update)
        self.assertEqual(feedback.run_state, "running")
        for _ in range(60):
            sim.step(p.FIXED_DT, {"nozzles": [0.]*4+[1.]*4+[0.]*2}, observer=feedback.update)
        self.assertTrue(sim.airborne)
        self.assertEqual(feedback.run_state, "invalid")
        self.assertEqual(feedback.run_reason, "Left level ground")
        self.assertEqual(feedback.rear_force, 0.)
        self.assertEqual(feedback.intensity, 0.)

    def test_reverse_departure_and_vertical_fall_are_not_zero_to_sixty(self):
        sim, feedback = p.Simulation("flat"), f.SpeedFeedback()
        feedback.reset(sim)
        for _ in range(120):
            sim.step(p.FIXED_DT, {"throttle": -1.}, observer=feedback.update)
        self.assertEqual(feedback.run_state, "waiting")
        self.assertIsNone(feedback.run_time)
        self.assertLess(feedback.forward_mph, 0.)
        sim.reset("flat")
        sim.y, sim.vy = 100., -100.
        feedback.reset(sim)
        sim.step(.1, {}, observer=feedback.update)
        self.assertEqual(feedback.run_state, "waiting")
        self.assertEqual(feedback.speed_mph, 0.)
        self.assertEqual(feedback.intensity, 0.)

    def test_collision_invalidates_attempt_and_retains_elapsed_time(self):
        wall = {"x": 0., "z": -7., "length": 1., "width": 8., "height": 3., "id": "wall"}
        sim, feedback = p.Simulation("flat", (wall,)), f.SpeedFeedback()
        feedback.reset(sim)
        for _ in range(240):
            sim.step(p.FIXED_DT, driving(True), observer=feedback.update)
            if feedback.run_state == "invalid":
                break
        self.assertEqual(feedback.run_state, "invalid")
        self.assertEqual(feedback.run_reason, "Impact interrupted run")
        elapsed = feedback.run_time
        for _ in range(60):
            sim.step(p.FIXED_DT, {}, observer=feedback.update)
        self.assertEqual(feedback.run_time, elapsed)

    def test_completed_time_is_retained_until_next_stationary_launch(self):
        sim, feedback = measured(True)
        result = feedback.run_time
        for _ in range(1200):
            sim.step(p.FIXED_DT, {"brake": 1.}, observer=feedback.update)
            if feedback._rest_time > .5:
                break
        self.assertTrue(feedback._armed)
        self.assertEqual(feedback.run_state, "complete")
        self.assertEqual(feedback.run_time, result)
        sim.step(p.FIXED_DT, driving(), observer=feedback.update)
        self.assertEqual(feedback.run_state, "running")
        self.assertAlmostEqual(feedback.run_time, p.FIXED_DT)

    def test_reset_clears_completed_run_and_visual_history(self):
        sim, feedback = measured(True)
        sim.reset("flat")
        feedback.reset(sim)
        self.assertEqual(feedback.run_state, "ready")
        self.assertIsNone(feedback.run_time)
        self.assertEqual(feedback.intensity, 0.)
        self.assertEqual(feedback.speed_mph, 0.)


if __name__ == "__main__":
    unittest.main()
