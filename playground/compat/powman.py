"""Power and wake are hardware-only in this preview."""
WAKE_BUTTON_A = 0
WAKE_BUTTON_B = 1
WAKE_BUTTON_C = 2
WAKE_BUTTON_UP = 3
WAKE_BUTTON_DOWN = 4
WAKE_UNKNOWN = 255

def get_wake_reason():
    return WAKE_UNKNOWN

def sleep(*args):
    raise NotImplementedError('sleep needs badge hardware')

goto_dormant_for = sleep
reset_into_msc = sleep
