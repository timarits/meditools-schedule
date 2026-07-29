"""medschedule.py -- pure schedule engine for the MediTools Presto device.

No hardware or MicroPython-specific imports: this file runs on both
MicroPython (on the Presto) and CPython (for the unit tests in /tests).

All times are handled as minutes-since-midnight (int) or seconds-since-
midnight (int) in LOCAL time (Europe/Amsterdam). Timezone/DST conversion
helpers are at the bottom.
"""

DAY_MIN = 24 * 60

STATE_RED = "RED"        # no eating allowed
STATE_GREEN = "GREEN"    # eating allowed
STATE_NIGHT = "NIGHT"    # night mode (dim clock)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def hhmm_to_min(s):
    """'07:30' -> 450"""
    parts = s.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def min_to_hhmm(m):
    """450 -> '07:30' (wraps past midnight)"""
    m = int(m) % DAY_MIN
    return "%02d:%02d" % (m // 60, m % 60)


DEFAULT_CONFIG = {
    "version": 1,
    "dose_times": ["07:00", "10:00", "13:00", "16:00", "19:00", "22:00"],
    # Dose times that must never make a sound. The screen and the LEDs still
    # signal, and the dose is still logged taken/missed exactly as usual --
    # only the buzzer is held silent. Used for doses at times when waking the
    # house would do more harm than the reminder does good.
    "silent_doses": [],
    "no_eat_before_min": 60,
    "no_eat_after_min": 30,
    "night": {"start": "22:30", "end": "06:00"},
    "alarm": {"max_minutes": 30, "hold_to_confirm_ms": 1500},
    "poll_interval_min": 5,
    "language": "nl",
}


def validate(raw):
    """Return None if raw is a usable config dict, else an error string."""
    if not isinstance(raw, dict):
        return "config is not an object"
    doses = raw.get("dose_times")
    if not isinstance(doses, list) or not doses:
        return "dose_times missing or empty"
    try:
        mins = [hhmm_to_min(d) for d in doses]
    except (ValueError, IndexError, AttributeError):
        return "dose_times contains an invalid time"
    for m in mins:
        if not 0 <= m < DAY_MIN:
            return "dose time out of range"
    silent = raw.get("silent_doses", [])
    if not isinstance(silent, list):
        return "silent_doses must be a list"
    try:
        for s in silent:
            m = hhmm_to_min(s)
            if not 0 <= m < DAY_MIN:
                return "silent dose time out of range"
    except (ValueError, IndexError, AttributeError):
        return "silent_doses contains an invalid time"
    for key in ("no_eat_before_min", "no_eat_after_min"):
        v = raw.get(key, 0)
        if not isinstance(v, int) or v < 0 or v > 360:
            return "%s must be an int between 0 and 360" % key
    leds = raw.get("leds", {})
    if not isinstance(leds, dict):
        return "leds must be an object"
    idx = leds.get("indices", None)
    if idx is not None:
        if not isinstance(idx, list) or not idx:
            return "leds.indices must be null or a non-empty list"
        for i in idx:
            if not isinstance(i, int) or not 0 <= i < 64:
                return "leds.indices must be small non-negative integers"
    if "brightness" in leds:
        b = leds["brightness"]
        if not isinstance(b, (int, float)) or not 0.0 <= b <= 1.0:
            return "leds.brightness must be between 0 and 1"

    backlight = raw.get("backlight", {})
    if not isinstance(backlight, dict):
        return "backlight must be an object"
    for key in ("day", "night", "night_wake"):
        if key in backlight:
            v = backlight[key]
            if not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
                return "backlight.%s must be between 0 and 1" % key

    night = raw.get("night", {})
    if night:
        try:
            hhmm_to_min(night.get("start", "23:00"))
            hhmm_to_min(night.get("end", "06:00"))
        except (ValueError, IndexError, AttributeError):
            return "night start/end invalid"
    return None


def build(raw):
    """Merge raw config over defaults and pre-compute the schedule."""
    cfg = {}
    for k, v in DEFAULT_CONFIG.items():
        cfg[k] = raw.get(k, v) if isinstance(raw, dict) else v
    # normalise nested dicts against defaults
    for nested in ("night", "alarm"):
        merged = dict(DEFAULT_CONFIG[nested])
        if isinstance(cfg.get(nested), dict):
            merged.update(cfg[nested])
        cfg[nested] = merged

    doses = sorted(set(hhmm_to_min(d) for d in cfg["dose_times"]))
    before = int(cfg["no_eat_before_min"])
    after = int(cfg["no_eat_after_min"])

    # Red (no-eat) intervals around every dose, replicated for the previous
    # and next day so windows that cross midnight behave correctly.
    reds = []
    for off in (-DAY_MIN, 0, DAY_MIN):
        for d in doses:
            reds.append((d + off - before, d + off + after))
    reds.sort()
    # merge overlapping intervals (possible if doses are close together)
    merged_reds = []
    for a, b in reds:
        if merged_reds and a <= merged_reds[-1][1]:
            if b > merged_reds[-1][1]:
                merged_reds[-1] = (merged_reds[-1][0], b)
        else:
            merged_reds.append((a, b))

    cfg["_doses"] = doses
    cfg["_silent"] = set(hhmm_to_min(d) for d in cfg.get("silent_doses") or [])
    cfg["_before"] = before
    cfg["_after"] = after
    cfg["_reds"] = merged_reds
    cfg["_night_start"] = hhmm_to_min(cfg["night"]["start"])
    cfg["_night_end"] = hhmm_to_min(cfg["night"]["end"])
    return cfg


# ---------------------------------------------------------------------------
# State engine
# ---------------------------------------------------------------------------

def in_night(now_min, cfg):
    ns, ne = cfg["_night_start"], cfg["_night_end"]
    if ns == ne:
        return False
    if ns < ne:
        return ns <= now_min < ne
    return now_min >= ns or now_min < ne  # window wraps midnight


def next_dose_min(now_min, cfg):
    """Minute-of-day of the next dose STRICTLY after now_min.
    May return a value >= 1440 meaning 'tomorrow'."""
    for off in (0, DAY_MIN):
        for d in cfg["_doses"]:
            if d + off > now_min:
                return d + off
    return cfg["_doses"][0] + DAY_MIN  # unreachable, but safe


def get_state(now_sec, cfg):
    """Compute the display state for now_sec (seconds since local midnight).

    Returns a dict:
      state          STATE_RED / STATE_GREEN / STATE_NIGHT
      remaining_sec  seconds until this state ends (countdown value)
      until_min      minute-of-day when the state ends (for 'until HH:MM')
      next_dose_min  minute-of-day of the next upcoming dose (may be >=1440)
    """
    now_min = now_sec // 60

    state = STATE_GREEN
    end_min = None
    for a, b in cfg["_reds"]:
        if a * 60 <= now_sec < b * 60:
            state = STATE_RED
            end_min = b
            break
    if state == STATE_GREEN:
        for a, b in cfg["_reds"]:
            if a * 60 > now_sec:
                end_min = a
                break

    nd = next_dose_min(now_min, cfg)

    # RED takes priority over night: after the evening dose the screen stays
    # red until the no-eat window ends, and only then dims to night mode.
    if state != STATE_RED and in_night(now_min, cfg):
        ne = cfg["_night_end"]
        if ne <= now_min:
            ne += DAY_MIN
        return {
            "state": STATE_NIGHT,
            "remaining_sec": ne * 60 - now_sec,
            "until_min": ne,
            "next_dose_min": nd,
            "awaiting_dose": False,
            "dose_remaining_sec": nd * 60 - now_sec,
        }

    # A red window has two halves that mean different things to the person
    # reading it. Before the dose, what matters is "your medicine is coming";
    # after it, "you still cannot eat". `awaiting_dose` says which half we are
    # in: the dose is still ahead of us and falls inside this same red window.
    # Comparing against end_min rather than the dose time itself is what makes
    # this correct when two doses are close enough that their red windows
    # merged -- then the window has several before-halves, one per dose.
    return {
        "state": state,
        "remaining_sec": end_min * 60 - now_sec,
        "until_min": end_min,
        "next_dose_min": nd,
        "awaiting_dose": state == STATE_RED and nd <= end_min,
        "dose_remaining_sec": nd * 60 - now_sec,
    }


# ---------------------------------------------------------------------------
# Dose instances
#
# A dose *instance* is one occurrence of one dose time on one calendar day,
# expressed as an absolute minute count: days_from_civil(y, m, d) * 1440 plus
# the minute-of-day. Working in absolute minutes rather than minute-of-day is
# what makes alarm windows that cross midnight behave correctly, and it makes
# every instance uniquely identifiable -- so bookkeeping survives reboots, day
# rollovers and DST changes without special cases.
# ---------------------------------------------------------------------------

def days_from_civil(y, m, d):
    """Days since 1970-01-01 for a proleptic Gregorian date (Hinnant's
    algorithm -- integer arithmetic only, so it runs on MicroPython)."""
    if m <= 2:
        y -= 1
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m - 3 if m > 2 else m + 9) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z):
    """Inverse of days_from_civil: days since 1970-01-01 -> (y, m, d)."""
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    return (y + 1 if m <= 2 else y, m, d)


def to_abs_min(date, min_of_day):
    """(y, m, d) + minute-of-day -> absolute minutes since the epoch."""
    return days_from_civil(date[0], date[1], date[2]) * DAY_MIN + int(min_of_day)


def abs_to_min_of_day(abs_min):
    """Absolute minutes -> minute-of-day (0..1439)."""
    return abs_min % DAY_MIN


def dose_instances(cfg, from_abs, to_abs):
    """Sorted absolute minutes of every dose instance in [from_abs, to_abs)."""
    if to_abs <= from_abs:
        return []
    out = []
    day = from_abs // DAY_MIN
    last_day = (to_abs - 1) // DAY_MIN
    while day <= last_day:
        base = day * DAY_MIN
        for d in cfg["_doses"]:
            a = base + d
            if from_abs <= a < to_abs:
                out.append(a)
        day += 1
    out.sort()
    return out


def due_instances(now_abs, cfg):
    """Dose instances whose alarm window covers now: dose <= now < dose + max.
    Used to decide whether the medicine alarm should be sounding."""
    max_min = int(cfg["alarm"]["max_minutes"])
    return [a for a in dose_instances(cfg, now_abs - max_min + 1, now_abs + 1)
            if a <= now_abs < a + max_min]


def is_silent(dose_abs, cfg):
    """True if this dose instance must not sound the buzzer.

    Silencing is a property of the clock time, so it applies to that dose on
    every day, and it changes nothing else: the alarm still shows, still needs
    confirming, and is still logged taken or missed.
    """
    return (dose_abs % DAY_MIN) in cfg["_silent"]


def expired_instances(cfg, from_abs, now_abs):
    """Dose instances whose alarm window *closed* within [from_abs, now_abs].

    Note the range applies to the moment the window closes, not the moment the
    dose was due: a dose at 16:00 with a 30-minute alarm is only reported here
    from 16:30 onwards. These are the candidates for 'missed'.
    """
    max_min = int(cfg["alarm"]["max_minutes"])
    return [a for a in dose_instances(cfg, from_abs - max_min, now_abs)
            if from_abs <= a + max_min <= now_abs]


# ---------------------------------------------------------------------------
# Europe/Amsterdam timezone (CET/CEST) from a UTC date/time
# ---------------------------------------------------------------------------

def _dow(y, m, d):
    """Day of week, 0=Sunday..6=Saturday (Sakamoto's algorithm)."""
    t = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    if m < 3:
        y -= 1
    return (y + y // 4 - y // 100 + y // 400 + t[m - 1] + d) % 7


def _last_sunday(y, m):
    """Day number of the last Sunday of a 31-day month (March/October)."""
    return 31 - _dow(y, m, 31)


def nl_offset_hours(y, mo, d, h):
    """UTC offset (hours) for Europe/Amsterdam at the given UTC date/time.
    DST (UTC+2) runs from the last Sunday of March 01:00 UTC until the
    last Sunday of October 01:00 UTC; otherwise UTC+1."""
    start = (3, _last_sunday(y, 3), 1)
    end = (10, _last_sunday(y, 10), 1)
    if start <= (mo, d, h) < end:
        return 2
    return 1
