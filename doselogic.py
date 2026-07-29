"""doselogic.py -- dose bookkeeping for the MediTools device.

Pure Python (no hardware or MicroPython-specific imports) so the same code
runs on the Presto and under CPython in the unit tests. All file access goes
through an injected `store`, which the tests replace with an in-memory fake.

The tracker answers two questions for the main loop:

  active(now_abs, cfg)   is a medicine alarm supposed to be sounding?
  sync(now_abs, cfg)     which doses just went unanswered? (logs them 'missed')

and records the outcome of each dose exactly once, in `doselog.csv`.

Doses are identified by *absolute minute* (see medschedule.dose_instances),
never by minute-of-day. That is what lets the bookkeeping survive midnight,
reboots and DST changes without special cases: a dose that has been handled
can never be confused with the same clock time on another day.
"""

import medschedule as ms

STATE_FILE = "dosestate.json"
LOG_FILE = "doselog.csv"
LOG_HEADER = "date,time,scheduled,status,delay_min\n"

# How far back sync() will reconstruct missed doses after a gap (power cut,
# long WiFi outage). Beyond this the gap is adopted silently rather than
# filling the log with dozens of rows nobody will act on.
BACKFILL_LIMIT_MIN = 2 * 24 * 60

# Handled entries older than this are dropped, so dosestate.json stays small.
HANDLED_KEEP_MIN = 3 * 24 * 60

# NTP routinely nudges the clock by a few seconds; that is not time travel.
CLOCK_BACK_TOLERANCE_MIN = 2

TAKEN = "taken"
MISSED = "missed"


class FileStore:
    """Default store: dosestate.json + doselog.csv on the device's flash.

    Every method swallows its exceptions -- a full or flaky filesystem must
    never take the device down -- but records the last failure in
    `last_error` so selftest.py can report it.
    """

    def __init__(self, state_path=STATE_FILE, log_path=LOG_FILE):
        self.state_path = state_path
        self.log_path = log_path
        self.last_error = None

    def load_state(self):
        import json
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except Exception as e:          # missing or corrupt -> start fresh
            self.last_error = repr(e)
            return None

    def save_state(self, state):
        import json
        try:
            with open(self.state_path, "w") as f:
                json.dump(state, f)
            return True
        except Exception as e:
            self.last_error = repr(e)
            return False

    def append_log(self, line):
        try:
            try:
                with open(self.log_path) as f:
                    fresh = not f.read(1)
            except OSError:
                fresh = True
            with open(self.log_path, "a") as f:
                if fresh:
                    f.write(LOG_HEADER)
                f.write(line)
            return True
        except Exception as e:
            self.last_error = repr(e)
            return False


class MemoryStore:
    """In-memory store for the tests and the desktop simulator."""

    def __init__(self, state=None):
        self.state = state
        self.lines = []
        self.last_error = None

    def load_state(self):
        return self.state

    def save_state(self, state):
        self.state = dict(state)
        return True

    def append_log(self, line):
        if not self.lines:
            self.lines.append(LOG_HEADER)
        self.lines.append(line)
        return True


def format_row(dose_abs, now_abs, now_sec, status):
    """One doselog.csv line: date,time,scheduled,status,delay_min."""
    y, mo, d = ms.civil_from_days(now_abs // ms.DAY_MIN)
    delay = max(0, now_abs - dose_abs)
    return "%04d-%02d-%02d,%02d:%02d:%02d,%s,%s,%d\n" % (
        y, mo, d,
        now_sec // 3600, (now_sec % 3600) // 60, now_sec % 60,
        ms.min_to_hhmm(dose_abs % ms.DAY_MIN), status, delay)


class DoseTracker:
    """Remembers which dose instances have been answered, and why."""

    def __init__(self, store):
        self.store = store
        self.reload()

    def reload(self):
        """Re-read persisted state from the store.

        Used at construction, and again after a remote maintenance command
        deletes the state file: the tracker must forget what it was holding in
        RAM rather than write it straight back out on the next save.
        """
        self.handled = set()
        self.last_seen = None
        state = self.store.load_state()
        if isinstance(state, dict):
            try:
                self.handled = set(int(x) for x in state.get("handled", []))
                seen = state.get("last_seen_abs")
                self.last_seen = None if seen is None else int(seen)
            except (TypeError, ValueError):
                self.handled = set()    # corrupt state: behave like first boot
                self.last_seen = None

    # -- queries ------------------------------------------------------------

    def active(self, now_abs, cfg):
        """The dose instance whose alarm should be sounding, or None."""
        for a in ms.due_instances(now_abs, cfg):
            if a not in self.handled:
                return a
        return None

    def is_handled(self, dose_abs):
        return dose_abs in self.handled

    def logged_today(self, now_abs):
        """How many dose instances from today have been answered.

        Reported in the hourly status push, so the day's progress can be seen
        remotely without downloading the whole log.
        """
        start = (now_abs // ms.DAY_MIN) * ms.DAY_MIN
        return len([a for a in self.handled if start <= a < start + ms.DAY_MIN])

    # -- transitions --------------------------------------------------------

    def sync(self, now_abs, cfg, now_sec=None):
        """Advance to now_abs, logging every dose whose alarm window closed
        without a confirmation. Returns the list of newly-missed instances.

        Called on every loop iteration; cheap and idempotent.
        """
        missed = []

        if self.last_seen is None:
            # First boot on this device. Nothing is known about earlier doses,
            # so adopt them silently -- inventing 'missed' rows would be a lie.
            self._adopt(now_abs, cfg)
        elif now_abs < self.last_seen - CLOCK_BACK_TOLERANCE_MIN:
            # Clock jumped backwards (NTP corrected a wrong RTC). The gap tells
            # us nothing, so re-adopt instead of logging phantom misses.
            self._adopt(now_abs, cfg)
        else:
            start = self.last_seen
            if now_abs - start > BACKFILL_LIMIT_MIN:
                # Gap too long to reconstruct usefully: silently accept the old
                # part, then report only the most recent BACKFILL_LIMIT_MIN.
                cutoff = now_abs - BACKFILL_LIMIT_MIN
                for a in ms.expired_instances(cfg, start, cutoff):
                    self.handled.add(a)
                start = cutoff
            for a in ms.expired_instances(cfg, start, now_abs):
                if a not in self.handled:
                    self.handled.add(a)
                    missed.append(a)

        self.last_seen = now_abs
        self._prune(now_abs)

        if now_sec is None:
            now_sec = (now_abs % ms.DAY_MIN) * 60
        for a in missed:
            self.store.append_log(format_row(a, now_abs, now_sec, MISSED))
        self._save()
        return missed

    def confirm(self, dose_abs, now_abs, now_sec=None):
        """Record that this dose was taken (the screen was held long enough)."""
        if dose_abs in self.handled:
            return False
        self.handled.add(dose_abs)
        if now_sec is None:
            now_sec = (now_abs % ms.DAY_MIN) * 60
        self.store.append_log(format_row(dose_abs, now_abs, now_sec, TAKEN))
        self._save()
        return True

    def on_schedule_change(self, now_abs, cfg):
        """A new schedule arrived. Dose times that were just added and whose
        alarm window is already in the past must not fire retroactively."""
        self._adopt(now_abs, cfg)
        self._save()

    # -- internals ----------------------------------------------------------

    def _adopt(self, now_abs, cfg):
        """Mark every already-closed dose window as handled without logging."""
        for a in ms.expired_instances(cfg, now_abs - HANDLED_KEEP_MIN, now_abs):
            self.handled.add(a)

    def _prune(self, now_abs):
        cutoff = now_abs - HANDLED_KEEP_MIN
        self.handled = set(a for a in self.handled if a >= cutoff)

    def _state(self):
        return {"handled": sorted(self.handled), "last_seen_abs": self.last_seen}

    def _save(self):
        self.store.save_state(self._state())
