"""selftest.py -- check that this Presto can actually run MediTools.

Run it from Thonny's shell (or `mpremote run device/selftest.py`) after
uploading. It probes every hardware and library name main.py depends on and
prints a PASS/FAIL line for each, so a firmware or upload problem shows up as
one clear failure instead of a blank screen.

It draws each screen briefly at the end -- watch the panel.
"""

import time

RESULTS = []

print("=== MediTools selftest ===")


def record(ok, name, detail):
    RESULTS.append((ok, name, detail))
    print("%-5s %-34s %s" % ("PASS" if ok else "FAIL", name, detail))


def check(name, fn):
    """Run one probe. Results print as they happen, so a hang is diagnosable
    from the last line printed rather than losing the whole report."""
    try:
        detail = fn()
        record(True, name, "" if detail is None else str(detail))
    except Exception as e:
        record(False, name, repr(e))


# --- libraries ---------------------------------------------------------------

check("import presto", lambda: __import__("presto") and None)
check("import picovector", lambda: __import__("picovector") and None)
check("import requests", lambda: __import__("requests") and None)
check("import ntptime", lambda: __import__("ntptime") and None)

import presto as presto_mod

check("presto.Buzzer exists", lambda: bool(presto_mod.Buzzer) and None)

# --- display -----------------------------------------------------------------

p = presto_mod.Presto(full_res=True, ambient_light=False)
display = p.display

check("full-res bounds is 480x480",
      lambda: "OK" if display.get_bounds() == (480, 480)
      else "got %r" % (display.get_bounds(),))
check("Presto.NUM_LEDS", lambda: presto_mod.Presto.NUM_LEDS)
check("set_led_rgb", lambda: p.set_led_rgb(0, 0, 0, 0))
check("set_backlight", lambda: p.set_backlight(1.0))
check("display.line with thickness",
      lambda: display.line(0, 0, 1, 1, 3))
check("touch.poll / touch.state",
      lambda: (p.touch.poll(), "pressed" if p.touch.state else "not pressed")[1])
check("buzzer", lambda: presto_mod.Buzzer(43).set_tone(-1))

# --- fonts -------------------------------------------------------------------

import painter                                          # noqa: E402
import theme as T                                       # noqa: E402

paint = painter.PrestoPainter(display)
check("font engine",
      lambda: "PicoVector (Figtree)" if isinstance(paint.font, painter.VectorFont)
      else "BITMAP FALLBACK -- .af fonts missing, screens will not match design")
check("measure 176px numeral", lambda: paint.measure("83", T.T_COUNTDOWN))
check("countdown fits the panel",
      lambda: "OK" if paint.measure("888", T.T_COUNTDOWN) < T.WIDTH
      else "888 needs %d px" % paint.measure("888", T.T_COUNTDOWN))

# --- application modules -----------------------------------------------------

import doselogic                                        # noqa: E402
import medschedule as ms                                # noqa: E402
import netsync                                          # noqa: E402
import texts                                            # noqa: E402
import ui                                               # noqa: E402

check("schedule.json present and valid", lambda: (
    "missing -- defaults will be used"
    if netsync.load_local_schedule() is None
    else ms.validate(netsync.load_local_schedule()) or "OK"))
check("build schedule",
      lambda: len(ms.build(netsync.load_local_schedule()
                           or ms.DEFAULT_CONFIG)["_doses"]))

# --- filesystem --------------------------------------------------------------


def _fs():
    store = doselogic.FileStore("selftest_state.json", "selftest_log.csv")
    if not store.save_state({"handled": [1], "last_seen_abs": 1}):
        raise OSError("cannot write state: %s" % store.last_error)
    if store.load_state().get("last_seen_abs") != 1:
        raise OSError("state did not round-trip")
    if not store.append_log("x,y,z,taken,0\n"):
        raise OSError("cannot write log: %s" % store.last_error)
    import os
    os.remove("selftest_state.json")
    os.remove("selftest_log.csv")
    return "read/write OK"


check("flash is writable", _fs)

# --- network -----------------------------------------------------------------

check("secrets.py", lambda: __import__("secrets").WIFI_SSID)
wifi = netsync.connect_wifi(p, retries=2)


def _wifi():
    if not wifi:
        raise OSError("not connected -- check secrets.py")
    return "connected"


check("WiFi", _wifi)
if wifi:
    ok = netsync.sync_ntp(retries=3)

    def _ntp():
        if not ok:
            raise OSError("no NTP -- device would sit on the error screen")
        return "clock set: %s UTC" % (time.localtime(),)

    check("NTP", _ntp)
    try:
        from secrets import SCHEDULE_URL
    except ImportError:
        SCHEDULE_URL = ""
    if SCHEDULE_URL:
        got, raw = netsync.fetch_remote_schedule(SCHEDULE_URL)
        check("remote schedule",
              lambda: "fetched, valid" if got and ms.validate(raw) is None
              else "fetch=%s validate=%s" % (got, ms.validate(raw)))
    else:
        record(True, "remote schedule", "SCHEDULE_URL empty (offline)")

# --- draw every screen -------------------------------------------------------

txt = texts.for_language("nl")
SCREENS = (
    ("red", lambda: ui.draw_state(paint, txt, True, 83, "10:30", "10:00")),
    ("green", lambda: ui.draw_state(paint, txt, False, 46, "12:00", "13:00")),
    ("alarm orange", lambda: ui.draw_alarm(paint, txt, True, 0.35)),
    ("alarm blue", lambda: ui.draw_alarm(paint, txt, False, 0.8)),
    ("taken", lambda: ui.draw_taken(paint, txt, "10:03")),
    ("night", lambda: ui.draw_night(paint, "23:37")),
    ("night wake", lambda: ui.draw_night_wake(paint, txt, 7, 25, "07:00", "23:37")),
    ("error", lambda: ui.draw_error(paint, txt)),
)

for name, draw in SCREENS:
    start = time.ticks_ms()
    led = draw()
    p.update()
    for i in range(presto_mod.Presto.NUM_LEDS):
        p.set_led_rgb(i, led[0], led[1], led[2])
    record(True, "draw %s" % name,
           "%d ms" % time.ticks_diff(time.ticks_ms(), start))
    time.sleep(1.2)

for i in range(presto_mod.Presto.NUM_LEDS):
    p.set_led_rgb(i, 0, 0, 0)

# --- report ------------------------------------------------------------------

failed = sum(1 for ok, _, _ in RESULTS if not ok)
print("\n%d checks, %d failed" % (len(RESULTS), failed))
