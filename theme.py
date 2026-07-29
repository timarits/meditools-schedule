"""theme.py -- design tokens for the MediTools device screens.

Transcribed from the design hand-off "Presto Screens.dc.html", direction 1b
("BAND -- split panel"). Every value here is a design decision; change it here
rather than in ui.py so the screens stay consistent with the spec.

Canvas is 480 x 480 (the Presto's full resolution). Colours are (r, g, b).
Type sizes are pixels on that 480 canvas.
"""

WIDTH = 480
HEIGHT = 480

# --- layout ----------------------------------------------------------------

BAND_H = 312          # coloured state band; the panel below fills the rest
PAD_X = 36
PAD_TOP = 30
PAD_BOTTOM = 30
CONTENT_W = WIDTH - 2 * PAD_X          # 408

# --- colours ---------------------------------------------------------------

WHITE = (255, 255, 255)
PANEL_BG = (12, 11, 10)                # #0C0B0A -- black panel under the band
PURE_BLACK = (0, 0, 0)

RED_BAND = (199, 48, 11)               # #C7300B
GREEN_BAND = (0, 115, 79)              # #00734F
ORANGE_BAND = (255, 138, 0)            # #FF8A00
BLUE_BAND = (18, 70, 224)              # #1246E0

LABEL = (145, 139, 130)                # #918B82 -- "Eten kan om"
DIVIDER = (46, 45, 44)                 # white @14% over PANEL_BG

ALARM_DARK_TEXT = (26, 17, 5)          # #1A1105 on orange
ALARM_HINT = (232, 226, 216)           # #E8E2D8
BAR_TRACK = (46, 45, 44)               # white @14% over PANEL_BG
BAR_FILL_A = (255, 138, 0)             # orange frame
BAR_FILL_B = (110, 147, 255)           # #6E93FF, blue frame

NIGHT_CLOCK = (46, 44, 42)             # #2E2C2A
NIGHT_WORD = (110, 106, 100)           # #6E6A64 "SLAAPTIJD"
NIGHT_BIG = (140, 135, 127)            # #8C877F countdown
NIGHT_SUB = (78, 74, 69)               # #4E4A45 "Medicijn om 07:00"
NIGHT_SMALL_CLOCK = (58, 55, 51)       # #3A3733
NIGHT_DIVIDER = (20, 20, 20)           # white @8% over black

ERROR_BG = (11, 10, 8)                 # #0B0A08
ERROR_BAND = (30, 27, 23)              # #1E1B17
ERROR_ACCENT = (255, 138, 0)           # top rule + eyebrow
ERROR_SUB = (154, 148, 139)            # #9A948B

# --- ambient LEDs ----------------------------------------------------------

LED_RED = (255, 40, 10)
LED_GREEN = (0, 190, 120)
LED_ORANGE = (255, 120, 0)
LED_BLUE = (0, 60, 255)
LED_OFF = (0, 0, 0)

# Which LEDs are lit. None means all of them; a tuple of indices lights only
# those and holds the rest off (the Presto has 7, indices 0..6, in a strip
# along the back). Run device/identify_leds.py to see which index is where.
LED_INDICES = None

# Every LED colour is scaled by this before being written. Full brightness is
# far too much for a bedroom; the colours above stay exactly as the design
# specifies them and are dimmed at the point of writing, so the screen and the
# LEDs can never drift apart.
#
# Note this multiplies per LED, so the light in the room also scales with how
# many are lit: all 7 at 0.30 is roughly four times the output of 2 at 0.25.
LED_BRIGHTNESS = 0.30

# --- backlight -------------------------------------------------------------

BL_DAY = 1.0
BL_NIGHT_WAKE = 0.4
BL_NIGHT = 0.15

# --- type ------------------------------------------------------------------


class Type(object):
    """One typographic role: size in px, weight, letter tracking in px."""

    __slots__ = ("size", "weight", "tracking")

    def __init__(self, size, weight, tracking=0):
        self.size = size
        self.weight = weight
        self.tracking = tracking


T_COUNTDOWN = Type(176, 900, -4)       # the 83 in "83 min"
T_COUNTDOWN_UNIT = Type(46, 700)       # the "min"
T_STATE = Type(46, 800, 2)             # NIET ETEN / ETEN MAG / MEDICIJN
T_VALUE = Type(38, 800)                # 10:30
T_LABEL = Type(26, 600)                # Eten kan om
T_CAPTION = Type(30, 600)              # tot je medicijn / tot je mag eten
T_ALARM_LINE = Type(78, 900, -1)       # Pak je / medicijn
T_ALARM_HINT = Type(30, 700)           # Houd het scherm vast
T_TAKEN = Type(76, 900, -1)            # GENOMEN!
T_TAKEN_SUB = Type(30, 600)            # 10:03 - goed gedaan
T_NIGHT_CLOCK = Type(96, 800, -2)
T_SLEEP_WORD = Type(40, 800, 3)        # SLAAPTIJD
T_SLEEP_BIG = Type(112, 900, -3)       # 7 ... 25
T_SLEEP_UNIT = Type(38, 700)           # u
T_ERROR_EYEBROW = Type(40, 800, 2)     # GEEN TIJD
T_ERROR_WORD = Type(62, 900, -1)       # KLOK NIET / GEZET
T_ERROR_SUB = Type(28, 600)            # Verbinden met WiFi...

# Baseline sits this fraction of the size below the top of a line box. Figtree
# has a tall x-height; 0.78 matches the design's line-height:1 blocks.
BASELINE_RATIO = 0.78


# --- remote overrides ------------------------------------------------------
#
# The device lives in someone else's house, so the settings most likely to
# need a nudge -- how bright the LEDs are and how many light up -- are part of
# the remotely-polled config rather than something that needs a USB visit.
# Everything else here stays a design decision and is changed in this file.

DEFAULTS = {
    "led_indices": LED_INDICES,
    "led_brightness": LED_BRIGHTNESS,
    "bl_day": BL_DAY,
    "bl_night": BL_NIGHT,
    "bl_night_wake": BL_NIGHT_WAKE,
}


def _clamp(value, low, high, fallback):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback
    return low if v < low else (high if v > high else v)


def apply(raw):
    """Override the tunable display settings from the polled config.

    Absent keys fall back to the defaults above, so deleting a key remotely
    restores the designed value rather than leaving the last one stuck.
    Values are clamped: a remote typo must not be able to black out the
    screen of a device nobody is standing next to.
    """
    global LED_INDICES, LED_BRIGHTNESS, BL_DAY, BL_NIGHT, BL_NIGHT_WAKE

    leds = raw.get("leds") if isinstance(raw, dict) else None
    leds = leds if isinstance(leds, dict) else {}
    idx = leds.get("indices", DEFAULTS["led_indices"])
    if idx is None:
        LED_INDICES = None
    else:
        try:
            LED_INDICES = tuple(int(i) for i in idx)
        except (TypeError, ValueError):
            LED_INDICES = DEFAULTS["led_indices"]
    LED_BRIGHTNESS = _clamp(leds.get("brightness", DEFAULTS["led_brightness"]),
                            0.0, 1.0, DEFAULTS["led_brightness"])

    bl = raw.get("backlight") if isinstance(raw, dict) else None
    bl = bl if isinstance(bl, dict) else {}
    # The day floor is deliberately not zero -- the daytime screen is the whole
    # point of the device, and it must stay readable whatever arrives remotely.
    BL_DAY = _clamp(bl.get("day", DEFAULTS["bl_day"]), 0.15, 1.0,
                    DEFAULTS["bl_day"])
    BL_NIGHT = _clamp(bl.get("night", DEFAULTS["bl_night"]), 0.0, 1.0,
                      DEFAULTS["bl_night"])
    BL_NIGHT_WAKE = _clamp(bl.get("night_wake", DEFAULTS["bl_night_wake"]),
                           0.05, 1.0, DEFAULTS["bl_night_wake"])


def baseline(top, style):
    """Baseline y for a line box whose top edge is at `top`."""
    return int(top + BASELINE_RATIO * style.size)


def mix(fg, bg, alpha):
    """Flatten a translucent colour onto an opaque one.

    The design uses opacity on a few elements (the 'min' unit, the confirm
    subtitle); the display has no alpha channel, so it is resolved here.
    """
    return tuple(int(round(fg[i] * alpha + bg[i] * (1.0 - alpha)))
                 for i in range(3))
