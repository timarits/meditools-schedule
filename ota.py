"""ota.py -- update the device's own code from GitHub, reversibly.

This is the riskiest thing in the project. The device is a medicine reminder
sitting on a bedside table in another house; a bad update that stops it
booting is not an inconvenience, it is a missed dose that nobody notices. So
the design is built around the update failing, not around it working:

  1. Fetch firmware.json, a manifest of filename -> sha256 and a version.
  2. Download every changed file to `<name>.new` and check its hash. Nothing
     touches the live files until all of them have arrived and verified, so a
     WiFi drop halfway through leaves a working device.
  3. Move each live file to `<name>.bak`, then `<name>.new` into place, and
     write an `otapending.json` marker naming every file involved.
  4. Reset.
  5. main.py -- which is never itself updated -- sees the marker and counts
     the attempt. app.py clears the marker once it has run healthily for a
     while. If the device reboots twice more without ever getting that far,
     main.py puts the .bak files back and reboots onto the old code.

So the worst case for a broken push is a couple of minutes of reboot loop,
then the device is back on the last version that worked, with the reason in
errorlog.txt.

main.py is deliberately not updatable: it is both the launcher and the
rollback, so it must not be something a bad update can replace. The
application itself lives in app.py and is fully updatable.

(The rollback started out in boot.py, the conventional place for it. It turned
out the Presto firmware never executes boot.py -- so it silently did nothing.
Hence main.py.)
"""

VERSION_FILE = "otaversion.json"
PENDING_FILE = "otapending.json"

# Never replaced by an update: main.py is the launcher and the rollback,
# secrets.py is local configuration, schedule.json is the live config polled
# separately (a release must not stamp on remote schedule edits), and the rest
# is this device's own state rather than anything belonging to a release.
PROTECTED = ("main.py", "secrets.py", "schedule.json",
             "otaversion.json", "otapending.json",
             "doselog.csv", "dosestate.json", "remotestate.json",
             "errorlog.txt")

# A file the manifest may not name, because writing outside the flash root or
# to a dotfile is not something a firmware release needs to do.
def _safe_name(name):
    return bool(isinstance(name, str) and name
                and "/" not in name and "\\" not in name
                and not name.startswith(".")
                and name not in PROTECTED
                and (name.endswith(".py") or name.endswith(".af")
                     or name.endswith(".json")))


def _read_json(path, default=None):
    import json
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    import json
    try:
        with open(path, "w") as f:
            json.dump(data, f)
        return True
    except Exception:
        return False


def _remove(path):
    import os
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def _exists(path):
    try:
        with open(path):
            return True
    except OSError:
        return False


def current_version():
    data = _read_json(VERSION_FILE, {})
    try:
        return int(data.get("version", 0))
    except (TypeError, ValueError):
        return 0


def sha256_file(path, chunk=512):
    """Hex sha256 of a file, read in chunks so a big .af does not blow RAM."""
    import hashlib
    try:
        import ubinascii as binascii
    except ImportError:      # CPython, for the tests
        import binascii
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return binascii.hexlify(h.digest()).decode()


def _download(url, dest, expected, feed):
    """Fetch one file and keep it only if it hashes to `expected`."""
    import requests
    r = None
    try:
        feed()
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return False
        with open(dest, "wb") as f:
            f.write(r.content)
    except Exception:
        _remove(dest)
        return False
    finally:
        if r is not None:
            try:
                r.close()
            except Exception:
                pass
        feed()
    try:
        if sha256_file(dest).lower() != expected.lower():
            _remove(dest)
            return False
    except Exception:
        _remove(dest)
        return False
    return True


def check_and_apply(manifest, base_url, feed=lambda: None, log=lambda m: None):
    """Apply `manifest` if it is newer. Returns True if a reset is needed.

    Does not reset by itself -- the caller decides when it is safe to reboot,
    which must never be in the middle of an alarm.
    """
    if not isinstance(manifest, dict):
        return False
    try:
        version = int(manifest.get("version", 0))
    except (TypeError, ValueError):
        return False
    if version <= current_version():
        return False
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        return False

    for name in files:
        if not _safe_name(name):
            log("ota: refusing %r" % name)
            return False

    # Skip files that are already exactly what the manifest asks for, so a
    # release that changes one module downloads one module.
    wanted = {}
    for name, digest in files.items():
        if not isinstance(digest, str) or len(digest) != 64:
            log("ota: bad digest for %s" % name)
            return False
        try:
            if _exists(name) and sha256_file(name).lower() == digest.lower():
                continue
        except Exception:
            pass
        wanted[name] = digest
    if not wanted:
        _write_json(VERSION_FILE, {"version": version})
        return False

    # Stage everything before touching anything live.
    staged = []
    for name, digest in wanted.items():
        if not _download(base_url + name, name + ".new", digest, feed):
            log("ota: download failed for %s" % name)
            for s in staged:
                _remove(s + ".new")
            return False
        staged.append(name)

    # Swap in. From here a failure is recoverable by the launcher.
    import os
    swapped = []
    for name in staged:
        try:
            _remove(name + ".bak")
            if _exists(name):
                os.rename(name, name + ".bak")
            os.rename(name + ".new", name)
            swapped.append(name)
        except Exception:
            log("ota: swap failed for %s" % name)
            break

    _write_json(PENDING_FILE, {"version": version, "files": swapped,
                               "attempts": 0,
                               "previous": current_version()})
    _write_json(VERSION_FILE, {"version": version})
    log("ota: staged v%d (%d files)" % (version, len(swapped)))
    return True


def commit():
    """Called once the new code has proven it runs. Drops the safety net."""
    pending = _read_json(PENDING_FILE)
    if not pending:
        return False
    for name in pending.get("files", []):
        _remove(name + ".bak")
    _remove(PENDING_FILE)
    return True


def rollback(log=lambda m: None):
    """Put the .bak files back. Called by the launcher, never by the app."""
    import os
    pending = _read_json(PENDING_FILE)
    if not pending:
        return False
    restored = 0
    for name in pending.get("files", []):
        if _exists(name + ".bak"):
            try:
                _remove(name)
                os.rename(name + ".bak", name)
                restored += 1
            except Exception:
                pass
    _write_json(VERSION_FILE, {"version": pending.get("previous", 0)})
    _remove(PENDING_FILE)
    log("ota: rolled back %d files to v%s"
        % (restored, pending.get("previous", 0)))
    return True


def note_boot_attempt(limit=2):
    """Count one boot under a pending update. True if it is time to roll back.

    main.py calls this before the app starts, so a crash on import is counted
    just the same as a crash in the loop.
    """
    pending = _read_json(PENDING_FILE)
    if not pending:
        return False
    attempts = int(pending.get("attempts", 0)) + 1
    pending["attempts"] = attempts
    _write_json(PENDING_FILE, pending)
    return attempts > limit
