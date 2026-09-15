"""Where the MAM session cookie (`mam_id`) comes from, and where a rotated one is kept.

Candidates, in order; the first one MAM accepts is used for the whole run:

1. mousehole state file (`Config/mousehole_state_file` or `MOUSEHOLE_STATE_FILE`). mousehole keeps the cookie current
   as the IP changes, so the file is read on every run and its value is sent verbatim, as mousehole itself does. It is
   never copied into the cookie store.
2. cookie store `<log_path>/cookies.json`: the `mam_id` MAM last handed back to booktree (MAM rotates the value). Plain
   JSON with mode 0600, replacing upstream's `cookies.pkl`, which was unpickled from a shared directory on every run.
3. `Config/session`, filled from `MAM_SESSION` or `MAM_SESSION_FILE` by myx_args when the config leaves it empty.

The cookie value itself is never printed; messages name the source only.
"""
import json
import os
import re
import stat
import tempfile

COOKIE_NAME = "mam_id"
# MAM sets mam_id with `Domain=.myanonamouse.net`; storing ours under the same key makes a rotation MAM sends back
# replace it in the jar instead of sitting next to it.
COOKIE_DOMAIN = ".myanonamouse.net"
# RFC 6265 cookie-octet: printable ASCII without space, `"`, `,`, `;` and `\`. Anything else cannot be a mam_id and
# would either break the Cookie header (echoing the value in the error) or smuggle a second cookie into it.
COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]+")
MAX_STATE_BYTES = 1 << 20          # a state or store file larger than this is not one of ours
STORE_NAME = "cookies.json"
LEGACY_STORE_NAME = "cookies.pkl"

_validated = None                  # (value, source) accepted by MAM in this run
_retiredLegacy = False


def reset():
    global _validated, _retiredLegacy
    _validated = None
    _retiredLegacy = False


def readSmallText(path, limit=MAX_STATE_BYTES):
    """The text of a small regular file; (text, None) or (None, reason). The reason never contains file content.

    The file is opened before it is inspected, and inspected through the descriptor: a FIFO planted at the path would
    otherwise block the run forever (O_NONBLOCK makes that open return, S_ISREG then rejects it), and a size check on
    the path could be raced by swapping in a larger file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return None, "file not found"
    except OSError as e:
        return None, type(e).__name__
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "not a regular file"
        if st.st_size > limit:
            return None, "file is unexpectedly large"
        with os.fdopen(fd, encoding="utf-8") as fh:
            fd = None
            return fh.read(limit + 1), None
    except Exception as e:                  # noqa: BLE001 - reported by type only, whatever the file holds
        return None, type(e).__name__
    finally:
        if fd is not None:
            os.close(fd)


def _readSmallJson(path):
    """Parse a small JSON file; (obj, None) or (None, reason). The reason never contains file content."""
    text, why = readSmallText(path)
    if text is None:
        return None, why
    try:
        return json.loads(text), None
    except Exception as e:                  # noqa: BLE001 - ValueError, RecursionError from deep nesting, ...
        return None, type(e).__name__


def validValue(value):
    """True when `value` is shaped like a cookie value MAM could have issued."""
    return isinstance(value, str) and COOKIE_VALUE.fullmatch(value) is not None


def readMousehole(path):
    """The cookie from a mousehole state file, or None. Accepts the current schema (`version: 2`, key `cookie`) and the
    legacy one (`currentCookie`), exactly as mousehole's own migration does."""
    state, why = _readSmallJson(path)
    if state is None:
        print(f"mousehole state file {path} could not be read ({why})")
        return None
    if not isinstance(state, dict):
        print(f"mousehole state file {path} is not a JSON object")
        return None
    value = state.get("cookie") if state.get("version") == 2 or "cookie" in state else None
    if not isinstance(value, str) or not value.strip():
        value = state.get("currentCookie")
    if not isinstance(value, str) or not value.strip():
        print(f"mousehole state file {path} holds no cookie yet")
        return None
    return value.strip()


def mouseholePath(cfg):
    path = cfg.get("Config/mousehole_state_file")
    if not path:
        path = os.environ.get("MOUSEHOLE_STATE_FILE")
    return str(path) if path else ""


def logPath(cfg):
    """Where the run's logs (and the cookie store) go: `Config/log_path`, else `./logs`, the same default
    myx_utilities.getLogPath applies to the CSV log."""
    path = cfg.get("Config/log_path")
    if not path:
        path = os.path.join(os.getcwd(), "logs")
    return str(path)


def storePath(log_path):
    return os.path.join(log_path, STORE_NAME)


def loadStore(log_path):
    """The mam_id saved by an earlier run, or None."""
    path = storePath(log_path)
    if not os.path.exists(path):
        return None
    data, why = _readSmallJson(path)
    if not isinstance(data, dict):
        print(f"Ignoring unreadable cookie store {path} ({why or 'not a JSON object'})")
        return None
    value = data.get(COOKIE_NAME)
    return value.strip() if isinstance(value, str) and value.strip() else None


def saveStore(log_path, value):
    """Write the store atomically, readable by the owner only. Returns True when written."""
    path = storePath(log_path)
    try:
        os.makedirs(log_path, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=log_path, prefix=STORE_NAME + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({COOKIE_NAME: value}, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError as e:
        print(f"Could not save the cookie store {path}: {type(e).__name__}")
        return False


def clearStore(log_path):
    try:
        os.remove(storePath(log_path))
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"Could not remove the cookie store: {type(e).__name__}")


def retireLegacyStore(log_path):
    """Upstream kept the session as a pickle; it is never loaded again (unpickling a file from a shared directory runs
    whatever is in it). Removed once, with a note; the next run re-validates from the config/env session."""
    global _retiredLegacy
    if _retiredLegacy:
        return
    _retiredLegacy = True
    legacy = os.path.join(log_path, LEGACY_STORE_NAME)
    if os.path.exists(legacy):
        try:
            os.remove(legacy)
            print(f"Removed the obsolete {LEGACY_STORE_NAME}; the MAM cookie is kept in {STORE_NAME} from now on")
        except OSError as e:
            print(f"Could not remove the obsolete {legacy} ({type(e).__name__}); it is never loaded, delete it by hand")


def candidates(cfg):
    """Ordered, de-duplicated (value, source) pairs to try. Empty when nothing is configured."""
    log_path = logPath(cfg)
    retireLegacyStore(log_path)
    found = []
    mh = mouseholePath(cfg)
    if mh:
        value = readMousehole(mh)
        if value:
            found.append((value, "mousehole"))
    value = loadStore(log_path)
    if value:
        found.append((value, "cookie store"))
    value = cfg.get("Config/session")
    if isinstance(value, str) and value.strip():
        found.append((value.strip(), "config"))
    seen, ordered = set(), []
    for value, source in found:
        if not validValue(value):
            print(f"Ignoring the MAM cookie from the {source}: not a valid cookie value")
            continue
        if value not in seen:
            seen.add(value)
            ordered.append((value, source))
    return ordered


def resolve(cfg):
    """The (value, source) to use now: the one MAM already accepted this run, else the first candidate."""
    if _validated is not None:
        return _validated
    found = candidates(cfg)
    return found[0] if found else ("", None)


def markValidated(value, source):
    global _validated
    _validated = (value, source)


def apply(sess, value):
    """Put the cookie in the session's jar (not a fixed header) so a rotation MAM sends back is picked up."""
    sess.cookies.set(COOKIE_NAME, value, domain=COOKIE_DOMAIN, path="/")


def current(sess, applied=None):
    """The mam_id the session would send now: the rotation MAM answered with when there is one, else `applied`.

    A Set-Cookie whose Domain differs from ours lands next to the applied cookie rather than replacing it, so every
    mam_id in the jar is looked at and one that differs from `applied` wins."""
    try:
        values = [c.value for c in sess.cookies if c.name == COOKIE_NAME and validValue(c.value)]
    except TypeError:
        values = []
    if not values:
        try:
            value = sess.cookies.get(COOKIE_NAME)
        except Exception:                   # noqa: BLE001 - CookieConflictError and fakes without get()
            value = None
        values = [value] if validValue(value) else []
    for value in values:
        if value != applied:
            return value
    return values[0] if values else applied


def accepted(cfg, sess, value, source):
    """MAM accepted `value`: use it (or the rotation MAM answered with) for the rest of the run and, unless mousehole
    owns the cookie, persist it in the store for later runs. Returns True when the store was written."""
    latest = current(sess, value) or value
    markValidated(latest, source)
    if source == "mousehole":
        return False
    log_path = logPath(cfg)
    if not latest:
        return False
    if latest == value and source == "cookie store":
        return False
    if saveStore(log_path, latest):
        print("Cookie updated...")
        return True
    return False
