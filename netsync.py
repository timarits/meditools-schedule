"""netsync.py -- WiFi, NTP time sync and remote schedule polling.

MicroPython-only (runs on the Presto). Network failures never crash the
app: every function returns a success flag and the caller falls back to
cached data.

Network calls are the slowest thing the device does, and some of them can
block for many seconds. main.py runs a hardware watchdog, so this module calls
`feed()` around anything slow -- otherwise a long connect would look like a
hang and trigger a reboot.
"""

import json
import time

SCHEDULE_FILE = "schedule.json"

# Kept short so no single call can outlast the watchdog window.
HTTP_TIMEOUT = 5

_feed = None


def set_watchdog(feed):
    """Register a callback to keep the watchdog alive during slow calls."""
    global _feed
    _feed = feed


def feed():
    if _feed is not None:
        try:
            _feed()
        except Exception:
            pass


def _sleep(seconds):
    """Sleep in short slices so the watchdog keeps being fed."""
    for _ in range(int(seconds * 4)):
        feed()
        time.sleep(0.25)


def connect_wifi(presto, retries=3):
    """Connect using WIFI_SSID / WIFI_PASSWORD from secrets.py."""
    for _ in range(retries):
        feed()
        try:
            if presto.connect():
                feed()
                return True
        except Exception:
            pass
        _sleep(2)
    return False


def sync_ntp(retries=5):
    """Set the RTC from an NTP server (RTC then holds UTC)."""
    import ntptime
    for _ in range(retries):
        feed()
        try:
            ntptime.settime()
            feed()
            return True
        except Exception:
            _sleep(2)
    return False


def load_local_schedule():
    """Read the cached/most recent schedule.json from flash."""
    try:
        with open(SCHEDULE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def save_local_schedule(raw):
    try:
        with open(SCHEDULE_FILE, "w") as f:
            json.dump(raw, f)
        return True
    except Exception:
        return False


def fetch_json(url):
    """GET a JSON document. Returns (ok, parsed-or-None)."""
    if not url:
        return False, None
    feed()
    try:
        import requests
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        try:
            if r.status_code != 200:
                return False, None
            data = r.json()
        finally:
            r.close()
        return True, data
    except Exception:
        return False, None
    finally:
        feed()


def fetch_remote_schedule(url):
    """GET the remote schedule.json. Returns (ok, dict-or-None)."""
    return fetch_json(url)


def sibling_url(url, name):
    """Another file next to `url` in the same repository directory.

    Lets secrets.py carry one SCHEDULE_URL and have commands.json and
    firmware.json found automatically beside it.
    """
    if not url or "/" not in url:
        return ""
    return url.rsplit("/", 1)[0] + "/" + name
