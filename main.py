"""MediTools -- medicine & eating-window assistant for the Pimoroni Presto.

Shows RED (do not eat) / GREEN (you can eat) with a countdown, sounds an
escalating alarm at every medicine time, and is confirmed by holding a finger
anywhere on the touchscreen. The schedule is polled from a remote
schedule.json (e.g. a raw GitHub URL) so it can be changed from anywhere.

This file is deliberately thin. It owns the hardware, the clock and the loop;
everything with rules in it lives in a module that runs under CPython too:

  medschedule.py  when is it red/green/night, when is a dose due
  doselogic.py    which doses have been answered, what gets logged
  ui.py           what each screen looks like  (theme.py = design tokens)
  painter.py      how ui.py reaches the display
  netsync.py      WiFi, NTP, schedule polling
  logsync.py      pushes doselog.csv back out so family can read it

Files expected next to this one on the Presto:
  secrets.py     WIFI_SSID / WIFI_PASSWORD / SCHEDULE_URL (+ optional log push)
  schedule.json  local (cached) schedule -- auto-updated from SCHEDULE_URL
"""

import time

from presto import Presto, Buzzer

import doselogic
import logsync
import medschedule as ms
import netsync
import ota
import remote
import texts
import theme as T
import ui
from painter import PrestoPainter

ERROR_LOG = "errorlog.txt"
ERROR_LOG_MAX = 8192

# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------

presto = Presto(full_res=True, ambient_light=False)
display = presto.display
touch = presto.touch
buzzer = Buzzer(43)
paint = PrestoPainter(display)

# A bedside reminder that hangs is worse than one that reboots: a hang is
# silent, and someone has to notice and pull the plug before the next dose.
#
# This is a soft watchdog rather than machine.WDT deliberately. The RP2350's
# hardware watchdog tops out around 8 seconds, which a slow WiFi association
# can legitimately exceed -- arming it would turn "the router is rebooting"
# into an unrecoverable boot loop. A timer-driven check with a two-minute
# window catches the failure that actually happens (a stuck loop) and cannot
# fire during any legitimate operation.
WATCHDOG_MS = 120000

_last_beat = time.ticks_ms()


def feed():
    global _last_beat
    _last_beat = time.ticks_ms()


def _watchdog(_timer):
    if time.ticks_diff(time.ticks_ms(), _last_beat) > WATCHDOG_MS:
        import machine
        machine.reset()


try:
    from machine import Timer
    try:
        _wd_timer = Timer(-1)          # virtual timer, most ports
    except Exception:
        _wd_timer = Timer()            # rp2 soft timer
    _wd_timer.init(period=10000, mode=Timer.PERIODIC, callback=_watchdog)
except Exception:
    pass                   # no timer available -- carry on unprotected

netsync.set_watchdog(feed)

try:
    from secrets import SCHEDULE_URL
except ImportError:
    SCHEDULE_URL = ""

# commands.json and firmware.json live beside schedule.json in the same repo,
# so one URL in secrets.py is enough. Either can be overridden explicitly.
try:
    from secrets import COMMANDS_URL
except ImportError:
    COMMANDS_URL = netsync.sibling_url(SCHEDULE_URL, "commands.json")
try:
    from secrets import FIRMWARE_URL
except ImportError:
    FIRMWARE_URL = netsync.sibling_url(SCHEDULE_URL, "firmware.json")

BOOT_MS = time.ticks_ms()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def set_leds(rgb):
    """Write the state colour to the ambient LEDs, dimmed.

    theme.LED_INDICES picks which ones light; anything not selected is driven
    to off rather than left alone, so a change never strands an LED on.
    """
    r = int(rgb[0] * T.LED_BRIGHTNESS)
    g = int(rgb[1] * T.LED_BRIGHTNESS)
    b = int(rgb[2] * T.LED_BRIGHTNESS)
    for i in range(Presto.NUM_LEDS):
        if T.LED_INDICES is None or i in T.LED_INDICES:
            presto.set_led_rgb(i, r, g, b)
        else:
            presto.set_led_rgb(i, 0, 0, 0)


def hhmm(sec):
    return "%02d:%02d" % (sec // 3600, (sec % 3600) // 60)


def up_minutes(sec):
    """Seconds -> whole minutes, rounded up.

    The countdown must never show '0 min' while the state is still running,
    so a partial minute always counts as one.
    """
    return 0 if sec <= 0 else (sec + 59) // 60


def now_local():
    """((y, m, d), seconds-since-local-midnight) for Europe/Amsterdam.
    The RTC holds UTC (set via NTP)."""
    utc = time.time()
    t = time.localtime(utc)
    off = ms.nl_offset_hours(t[0], t[1], t[2], t[3])
    lt = time.localtime(utc + off * 3600)
    return (lt[0], lt[1], lt[2]), lt[3] * 3600 + lt[4] * 60 + lt[5]


def time_is_valid():
    return time.localtime(time.time())[0] >= 2024


def log_error(exc):
    """Record a crash so it can be read back later. Best effort, never raises."""
    try:
        try:
            if _file_size(ERROR_LOG) > ERROR_LOG_MAX:
                open(ERROR_LOG, "w").close()
        except Exception:
            pass
        with open(ERROR_LOG, "a") as f:
            try:
                date, sec = now_local()
                f.write("%04d-%02d-%02d %s " % (date[0], date[1], date[2],
                                                hhmm(sec)))
            except Exception:
                f.write("?? ")
            try:
                import sys
                sys.print_exception(exc, f)
            except Exception:
                f.write(repr(exc))
            f.write("\n")
    except Exception:
        pass


def _file_size(path):
    with open(path) as f:
        f.seek(0, 2)
        return f.tell()


def log_line(message):
    """One line into errorlog.txt. Used for update progress, not just crashes."""
    try:
        with open(ERROR_LOG, "a") as f:
            try:
                date, sec = now_local()
                f.write("%04d-%02d-%02d %s " % (date[0], date[1], date[2],
                                                hhmm(sec)))
            except Exception:
                pass
            f.write(message + "\n")
    except Exception:
        pass


def alarm_tone(elapsed_ms):
    """Escalating beep pattern. Returns a frequency, or -1 for silence."""
    if elapsed_ms < 60000:             # first minute: polite double beep
        period, freq = 4000, 880
        windows = ((0, 250), (500, 750))
    elif elapsed_ms < 180000:          # minutes 1-3: more insistent
        period, freq = 2000, 988
        windows = ((0, 250), (500, 750))
    else:                              # after 3 min: hard to ignore
        period, freq = 1000, 1047
        windows = ((0, 300), (500, 800))
    phase = elapsed_ms % period
    for a, b in windows:
        if a <= phase < b:
            return freq
    return -1


def confirm_melody():
    for freq in (660, 880, 1100):
        buzzer.set_tone(freq)
        time.sleep(0.13)
    buzzer.set_tone(-1)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

raw = netsync.load_local_schedule()
if raw is None or ms.validate(raw) is not None:
    raw = ms.DEFAULT_CONFIG
cfg = ms.build(raw)
txt = texts.for_language(cfg.get("language", "nl"))
T.apply(raw)

store = doselogic.FileStore()
tracker = doselogic.DoseTracker(store)

last_backlight = None
last_signature = None
last_led = None


def set_backlight(level):
    global last_backlight
    if level != last_backlight:
        presto.set_backlight(level)
        last_backlight = level


def render(signature, draw, *args):
    """Draw only when something actually changed.

    Countdowns tick in whole minutes, so most of the time the screen is
    identical to the previous pass; repainting 5x a second would burn power
    and add nothing.
    """
    global last_signature, last_led
    if signature == last_signature:
        return
    led = draw(paint, *args)
    presto.update()
    if led != last_led:
        set_leds(led)
        last_led = led
    last_signature = signature


def show_error():
    set_backlight(T.BL_DAY)
    render(("error",), ui.draw_error, txt)


# The clock is not set until NTP succeeds; show the error screen straight away
# rather than a blank panel. Connecting is left to the loop's error branch, so
# there is exactly one piece of code that gets the device online and it runs
# under the watchdog.
show_error()

wifi_ok = False
ntp_ok = False

# Runtime state ---------------------------------------------------------------
alarm_started = {}       # dose_abs -> ticks_ms when its alarm began
hold_start = None        # ticks_ms when the current screen-hold began
taken_until = 0          # ticks_ms until which the 'GENOMEN!' flash shows
taken_at = "--:--"       # clock time printed on that flash
night_wake_until = 0     # ticks_ms until which the tapped night screen shows
last_poll = time.ticks_ms()
last_ntp = time.ticks_ms()
last_logpush = time.ticks_ms()
last_ota = time.ticks_ms()
ota_committed = False    # cleared the safety net for the running update?
reset_wanted = False     # an update is staged and waiting for a quiet moment

POLL_MS = max(1, int(cfg.get("poll_interval_min", 5))) * 60000
NTP_MS = 6 * 3600 * 1000
LOGPUSH_MS = 60 * 60 * 1000
OTA_MS = 30 * 60 * 1000
HOLD_MS = int(cfg["alarm"].get("hold_to_confirm_ms", 1500))

# How long the new code has to survive before the previous version is thrown
# away. Long enough to have drawn screens, polled, and written state at least
# once; short enough that the .bak files do not linger for hours.
OTA_COMMIT_MS = 90 * 1000


def apply_new_schedule(new_raw, now_abs):
    global cfg, txt, POLL_MS, HOLD_MS, last_signature, last_led, last_backlight
    cfg = ms.build(new_raw)
    txt = texts.for_language(cfg.get("language", "nl"))
    T.apply(new_raw)
    POLL_MS = max(1, int(cfg.get("poll_interval_min", 5))) * 60000
    HOLD_MS = int(cfg["alarm"].get("hold_to_confirm_ms", 1500))
    netsync.save_local_schedule(new_raw)
    # A dose time that was just added but whose alarm window is already in the
    # past must not fire retroactively.
    tracker.on_schedule_change(now_abs, cfg)
    # Force the next pass to repaint and to rewrite the LEDs and backlight:
    # brightness may have changed without the state changing at all.
    last_signature = None
    last_led = None
    last_backlight = None


def status_snapshot(now_abs):
    """What the device reports about itself, once an hour.

    Enough to answer "is it alive and is it working" from a phone, without
    anything that would identify a person if the log repo were ever exposed.
    """
    date, now_sec = now_local()
    st = ms.get_state(now_sec, cfg)
    try:
        errors = _file_size(ERROR_LOG)
    except Exception:
        errors = 0
    return {
        "seen": "%04d-%02d-%02d %s" % (date[0], date[1], date[2], hhmm(now_sec)),
        "state": st["state"],
        "uptime_h": time.ticks_diff(time.ticks_ms(), BOOT_MS) // 3600000,
        "next_dose": ms.min_to_hhmm(st["next_dose_min"]),
        "doses_logged_today": tracker.logged_today(now_abs),
        "firmware": ota.current_version(),
        "errorlog_bytes": errors,
        "wifi": wifi_ok,
    }


def run_commands():
    """Poll commands.json and carry out anything not already done."""
    if not COMMANDS_URL:
        return []
    ok, doc = netsync.fetch_json(COMMANDS_URL)
    if not ok or not doc:
        return []
    return remote.run(doc, doselogic.LOG_FILE, doselogic.STATE_FILE,
                      lambda: netsync.sync_ntp(retries=2),
                      lambda: __import__("machine").reset())


def background_jobs(now_ms, now_abs, busy):
    """Schedule polling, commands, NTP resync, log upload and status.

    Runs on every pass, including while an alarm is sounding -- an alarm can
    last half an hour and the device must not go deaf to the outside world for
    that long. Network calls that block for seconds are skipped while `busy`.
    """
    global raw, last_poll, last_ntp, last_logpush, last_ota, reset_wanted

    if SCHEDULE_URL and time.ticks_diff(now_ms, last_poll) > POLL_MS and not busy:
        last_poll = now_ms
        ok, new_raw = netsync.fetch_remote_schedule(SCHEDULE_URL)
        if ok and new_raw and ms.validate(new_raw) is None and new_raw != raw:
            raw = new_raw
            apply_new_schedule(new_raw, now_abs)
        # Commands ride along with the schedule poll: same cadence, and the
        # tracker state they may delete is not in use at this point.
        for done in run_commands():
            if done in ("clear_log", "clear_state"):
                tracker.reload()

    if time.ticks_diff(now_ms, last_ntp) > NTP_MS and not busy:
        last_ntp = now_ms
        netsync.sync_ntp(retries=1)

    if time.ticks_diff(now_ms, last_logpush) > LOGPUSH_MS and not busy:
        last_logpush = now_ms
        logsync.push(doselogic.LOG_FILE)
        logsync.push_status(status_snapshot(now_abs))

    if FIRMWARE_URL and time.ticks_diff(now_ms, last_ota) > OTA_MS and not busy:
        last_ota = now_ms
        ok, manifest = netsync.fetch_json(FIRMWARE_URL)
        if ok and manifest:
            base = netsync.sibling_url(FIRMWARE_URL, "")
            if ota.check_and_apply(manifest, base, feed, log_line):
                # Staged, but do not reboot here: the reset happens from the
                # main loop, which only allows it when no alarm is running.
                reset_wanted = True


# ---------------------------------------------------------------------------
# One pass of the loop
# ---------------------------------------------------------------------------

def tick():
    global ntp_ok, wifi_ok, hold_start, taken_until, taken_at
    global night_wake_until, last_signature

    global ota_committed
    feed()
    now_ms = time.ticks_ms()

    # The running code has stayed up long enough to be trusted: throw away the
    # previous version so the next update starts from a clean slate.
    if not ota_committed and time.ticks_diff(now_ms, BOOT_MS) > OTA_COMMIT_MS:
        ota_committed = True
        if ota.commit():
            log_line("ota: v%d confirmed good" % ota.current_version())

    # --- clock not set yet: nothing else can be trusted ----------------------
    if not (ntp_ok and time_is_valid()):
        show_error()
        wifi_ok = netsync.connect_wifi(presto, retries=1)
        ntp_ok = netsync.sync_ntp(retries=2) if wifi_ok else False
        if ntp_ok and time_is_valid():
            last_signature = None
        return 2.0

    date, now_sec = now_local()
    now_abs = ms.to_abs_min(date, now_sec // 60)

    tracker.sync(now_abs, cfg, now_sec)
    active = tracker.active(now_abs, cfg)

    # --- medicine alarm ------------------------------------------------------
    if active is not None:
        set_backlight(T.BL_DAY)
        if active not in alarm_started:
            alarm_started[active] = now_ms
        elapsed = time.ticks_diff(now_ms, alarm_started[active])

        touch.poll()
        if touch.state:
            if hold_start is None:
                hold_start = now_ms
            held = time.ticks_diff(now_ms, hold_start)
        else:
            hold_start = None
            held = 0

        if held >= HOLD_MS:
            tracker.confirm(active, now_abs, now_sec)
            silent = ms.is_silent(active, cfg)
            alarm_started.pop(active, None)
            hold_start = None
            buzzer.set_tone(-1)
            if not silent:
                confirm_melody()
            taken_until = time.ticks_add(now_ms, 2500)
            taken_at = hhmm(now_sec)
        else:
            # A silenced dose still shows and still has to be confirmed; only
            # the buzzer is held quiet.
            buzzer.set_tone(-1 if ms.is_silent(active, cfg)
                            else alarm_tone(elapsed))
            phase = (elapsed // 500) % 2 == 0
            frac = held / float(HOLD_MS)
            render(("alarm", phase, int(frac * 40)),
                   ui.draw_alarm, txt, phase, frac)
            background_jobs(now_ms, now_abs, busy=True)
            return 0.03

    # --- not alarming --------------------------------------------------------
    buzzer.set_tone(-1)
    hold_start = None
    for d in list(alarm_started):
        if tracker.is_handled(d):
            alarm_started.pop(d, None)

    if time.ticks_diff(taken_until, now_ms) > 0:
        set_backlight(T.BL_DAY)
        render(("taken", taken_at), ui.draw_taken, txt, taken_at)
        background_jobs(now_ms, now_abs, busy=True)
        return 0.05

    st = ms.get_state(now_sec, cfg)
    dose_hhmm = ms.min_to_hhmm(st["next_dose_min"])

    if st["state"] == ms.STATE_NIGHT:
        touch.poll()
        if touch.state:
            night_wake_until = time.ticks_add(now_ms, 10000)
        clock = hhmm(now_sec)
        if time.ticks_diff(night_wake_until, now_ms) > 0:
            set_backlight(T.BL_NIGHT_WAKE)
            left = up_minutes(st["next_dose_min"] * 60 - now_sec)
            render(("wake", left, clock), ui.draw_night_wake, txt,
                   left // 60, left % 60, dose_hhmm, clock)
        else:
            set_backlight(T.BL_NIGHT)
            render(("night", clock), ui.draw_night, clock)
    else:
        set_backlight(T.BL_DAY)
        red = st["state"] == ms.STATE_RED
        until = ms.min_to_hhmm(st["until_min"])
        if st["awaiting_dose"]:
            # First half of a red window: count toward the medicine.
            left = up_minutes(st["dose_remaining_sec"])
            caption = txt["until_med"]
        else:
            # Second half (or a green window): count toward eating.
            left = up_minutes(st["remaining_sec"])
            caption = txt["until_eat"] if red else None
        render(("state", red, left, until, dose_hhmm, caption),
               ui.draw_state, txt, red, left, until, dose_hhmm, caption)

    background_jobs(now_ms, now_abs, busy=False)

    # An update was staged. Reboot into it only from here -- a quiet moment
    # with no alarm sounding and no confirmation on screen.
    if reset_wanted:
        log_line("ota: rebooting into the new version")
        set_leds(T.LED_OFF)
        import machine
        machine.reset()

    return 0.2


# ---------------------------------------------------------------------------
# Main loop
#
# A bedside reminder must not be able to die quietly: any unexpected error is
# logged, shown on screen, and the loop carries on.
# ---------------------------------------------------------------------------

while True:
    try:
        time.sleep(tick())
    except Exception as exc:
        log_error(exc)
        try:
            buzzer.set_tone(-1)
            last_signature = None
            show_error()
        except Exception:
            pass
        time.sleep(2)
