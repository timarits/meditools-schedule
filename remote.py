"""remote.py -- maintenance commands the device polls and runs once.

The device lives in someone else's house. This is the channel for the jobs
that would otherwise need a USB cable: clearing a log that filled up with
test rows, forcing a clock resync, rebooting after a change.

Put a commands.json next to schedule.json in the same repo:

    {
      "id": "2026-07-30-01",
      "clear_log": true,
      "resync_clock": true
    }

`id` is what makes this safe. The device records the last id it executed and
ignores that id forever after, so a command sitting in a file that gets polled
every five minutes runs exactly once, not 288 times a day. Change the id to
run something new; leave it alone and nothing happens.

Nothing here can execute arbitrary code -- the only recognised commands are
the fixed set below, and an unknown key is ignored rather than being some
opening to run something the device was never meant to run.
"""

STATE_FILE = "remotestate.json"

# Every command the device will act on. Anything else in the file is ignored.
COMMANDS = ("clear_log", "clear_state", "resync_clock", "reboot")


def _load():
    import json
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    import json
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f)
        return True
    except Exception:
        return False


def last_id():
    return _load().get("last_command_id")


def record(command_id):
    data = _load()
    data["last_command_id"] = command_id
    _save(data)


def pending(raw):
    """Return the list of commands to run for this file, or [] if none.

    Returns [] when the file is malformed, has no id, or carries an id that
    has already been executed.
    """
    if not isinstance(raw, dict):
        return []
    command_id = raw.get("id")
    if not command_id or not isinstance(command_id, str):
        return []
    if command_id == last_id():
        return []
    return [c for c in COMMANDS if raw.get(c) is True]


def run(raw, log_path, state_path, resync, reboot):
    """Execute the pending commands and remember the id.

    The callables are injected so this module stays free of hardware and
    network imports and can be tested under CPython. Returns the list of
    commands that were actually carried out.
    """
    import os

    todo = pending(raw)
    if not todo:
        return []

    done = []
    for command in todo:
        try:
            if command == "clear_log":
                os.remove(log_path)
                done.append(command)
            elif command == "clear_state":
                os.remove(state_path)
                done.append(command)
            elif command == "resync_clock":
                if resync():
                    done.append(command)
            elif command == "reboot":
                done.append(command)
        except OSError:
            # A file that is already gone counts as cleared.
            if command in ("clear_log", "clear_state"):
                done.append(command)
        except Exception:
            pass

    # Record the id even if some command failed, so a command that cannot
    # succeed does not retry every five minutes forever.
    record(raw.get("id"))

    if "reboot" in done:
        reboot()
    return done
