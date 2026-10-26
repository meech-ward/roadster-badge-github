"""Display-only grounded look controller using calibrated badge lean.

The app owns contact/input gating. This controller never arms gestures, changes
vehicle controls or reads hardware. Its scalar state is reused each frame.
"""

MAX_YAW = .3490658503988659  # Twenty degrees, in radians.
MAX_HEIGHT = 1.3
RESPONSE_SECONDS = .20
_INFINITY = float('inf')


class GroundCamera:
    def __init__(self):
        self.reset()

    def reset(self):
        self.yaw = self.height = 0.
        self.active = False

    def update(self, dt, model, enabled):
        """Ease toward sensed lean, or back to normal when ground look ends.

        `tilt_x/y` already include the motion controller's calibrated four-degree
        dead zone. Read those sensed axes, never armed jet/flick channels, so
        ground look also works in Flick mode without triggering a gesture.
        """
        self.active = bool(enabled and model and model.get('calibrated') and
            model.get('tilt_valid') and not model.get('sensor_clipped'))
        target_yaw = target_height = 0.
        if self.active:
            target_yaw = max(-1., min(1., model.get('tilt_x', 0.))) * MAX_YAW
            # Raising the eye looks down at the car. Move it opposite the
            # requested look direction so an upward lean looks upward.
            target_height = -max(-1., min(1., model.get('tilt_y', 0.))) * MAX_HEIGHT
        if not 0. < dt < _INFINITY:
            return
        # A stalled frame must not snap the camera to a new view. This matches
        # the existing camera's bounded elapsed-time policy.
        dt = min(dt, .1)
        blend = dt / (RESPONSE_SECONDS + dt)
        self.yaw += (target_yaw - self.yaw) * blend
        self.height += (target_height - self.height) * blend
        if target_yaw == 0. and abs(self.yaw) < .0001:
            self.yaw = 0.
        if target_height == 0. and abs(self.height) < .0001:
            self.height = 0.
