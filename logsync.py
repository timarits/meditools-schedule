"""logsync.py -- pushes doselog.csv back out so family can read it remotely.

Without this the dose history only exists on the device's flash and can only
be read over USB, which defeats the point of the secondary user in
DESIGNER_BRIEF.md ("checks remotely whether doses were taken").

Configured entirely from secrets.py; if the keys are absent this module does
nothing and costs one dictionary lookup per hour.

    LOG_REPO   = "your-user/meditools-schedule"
    LOG_PATH   = "doselog.csv"
    LOG_TOKEN  = "github_pat_..."       fine-grained, Contents: read+write,
                                        limited to that one repository
    LOG_BRANCH = "main"                 optional, defaults to main

The GitHub Contents API needs the blob SHA of the file it is replacing, so a
push is a GET followed by a PUT. Both are best-effort: any failure leaves the
local file untouched and the next hourly attempt tries again.
"""

try:
    import ubinascii as binascii
except ImportError:          # CPython, for the tests
    import binascii


def _config():
    try:
        import secrets
    except ImportError:
        return None
    repo = getattr(secrets, "LOG_REPO", "")
    token = getattr(secrets, "LOG_TOKEN", "")
    if not repo or not token:
        return None
    return {
        "repo": repo,
        "path": getattr(secrets, "LOG_PATH", "doselog.csv"),
        "status_path": getattr(secrets, "STATUS_PATH", "status.json"),
        "token": token,
        "branch": getattr(secrets, "LOG_BRANCH", "main"),
    }


def _headers(cfg):
    return {
        "Authorization": "Bearer " + cfg["token"],
        "Accept": "application/vnd.github+json",
        "User-Agent": "MediTools",
        "Content-Type": "application/json",
    }


def _url(cfg, path):
    return "https://api.github.com/repos/%s/contents/%s" % (cfg["repo"], path)


def _remote_sha(requests, cfg, path):
    """Blob SHA of the file already in the repo, or None if it is not there."""
    r = None
    try:
        r = requests.get(_url(cfg, path) + "?ref=" + cfg["branch"],
                         headers=_headers(cfg), timeout=15)
        if r.status_code != 200:
            return None
        return r.json().get("sha")
    except Exception:
        return None
    finally:
        if r is not None:
            try:
                r.close()
            except Exception:
                pass


def _put(cfg, path, body, message):
    """Create or replace one file in the repo. True only on a confirmed write."""
    try:
        import json
        import requests
    except ImportError:
        return False

    payload = {
        "message": message,
        "content": binascii.b2a_base64(body).decode().strip(),
        "branch": cfg["branch"],
    }
    sha = _remote_sha(requests, cfg, path)
    if sha:
        payload["sha"] = sha

    r = None
    try:
        r = requests.put(_url(cfg, path), data=json.dumps(payload),
                         headers=_headers(cfg), timeout=20)
        return r.status_code in (200, 201)
    except Exception:
        return False
    finally:
        if r is not None:
            try:
                r.close()
            except Exception:
                pass


def push(log_path):
    """Upload the dose log. Returns True only on a confirmed write."""
    cfg = _config()
    if cfg is None:
        return False
    try:
        with open(log_path, "rb") as f:
            body = f.read()
    except Exception:
        return False            # nothing logged yet -- nothing to push
    if not body:
        return False
    return _put(cfg, cfg["path"], body, "doselog update")


def push_status(status):
    """Upload a status snapshot so the device can be checked on remotely.

    Deliberately separate from the dose log: this is written every hour
    whether or not anything happened, and it is what tells you the device is
    still alive rather than sitting dead on a shelf.
    """
    cfg = _config()
    if cfg is None:
        return False
    try:
        import json
        body = json.dumps(status).encode()
    except Exception:
        return False
    return _put(cfg, cfg["status_path"], body, "status update")
