"""External hints for matching (--hints <json>) and duration evidence.

A hints file lets a caller that knows more than the id3 tags (a request tracker, a review UI, a person) steer
the Audible match per release without touching the files:

    {
      "Megan Fate Marshman - Relaxed.m4b":            {"asin": "B0C5Q9XJ1K"},
      "/data/downloads/complete/audio/Some Folder":   {"candidates": ["B0ABC12345", "B0DEF67890"], "duration_min": 541},
      "Brad Thor - Takedown Unabridged - Complete":   {"title": "Takedown", "authors": ["Brad Thor"], "duration_min": 612}
    }

Keys are matched against the release as booktree names it (the folder under the source path, or the file
name for a loose file), the full path of the release folder, or the full path of any of its files.  Fields:

    asin          pinned ASIN: authoritative, the Audible product is accepted without a title/author check
    refresh       true: ignore cached search results and the "already processed" marker for this release
    candidates    up to MAX_CANDIDATES ASINs to fetch by ASIN and rank by duration, then fuzzy score
    duration_min  expected runtime in minutes (overrides the sum of the files' durations)
    title         search title to use instead of the id3 title
    authors       search author(s) to use instead of the id3 artist (string or list)

Duration is evidence: a candidate whose Audible runtime is within DURATION_TOLERANCE_MIN of the expected
runtime is preferred over one that is not, before fuzzy scores are compared.
"""
import json
import math
import os
import re
import tempfile

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
MAX_CANDIDATES = 10
MAX_AUTHORS = 10
MAX_TEXT_LEN = 300
MAX_FILE_BYTES = 16 * 1024 * 1024
DURATION_TOLERANCE_MIN = 2

_cache = {}


class HintsError(ValueError):
    pass


def isAsin(value):
    return bool(value) and bool(ASIN_RE.match(str(value).strip().upper()))


def _asin(value, where):
    v = str(value).strip().upper()
    if not ASIN_RE.match(v):
        raise HintsError(f"{where}: {value!r} is not an ASIN (10 letters/digits)")
    return v


def normalizeHint(raw, where="hint"):
    """Validate one hint object and return it in canonical form (unknown fields are dropped)."""
    if not isinstance(raw, dict):
        raise HintsError(f"{where}: expected an object, got {type(raw).__name__}")
    hint = {}
    if raw.get("asin"):
        hint["asin"] = _asin(raw["asin"], f"{where}.asin")
    if raw.get("candidates"):
        if not isinstance(raw["candidates"], list):
            raise HintsError(f"{where}.candidates: expected a list")
        cands = []
        for c in raw["candidates"]:
            a = _asin(c, f"{where}.candidates")
            if a not in cands:
                cands.append(a)
        if len(cands) > MAX_CANDIDATES:
            raise HintsError(f"{where}.candidates: at most {MAX_CANDIDATES} ASINs (got {len(cands)})")
        hint["candidates"] = cands
    if raw.get("duration_min") is not None:
        if isinstance(raw["duration_min"], bool):
            raise HintsError(f"{where}.duration_min: expected minutes as a number")
        try:
            d = float(raw["duration_min"])
        except (TypeError, ValueError):
            raise HintsError(f"{where}.duration_min: expected minutes as a number")
        if not math.isfinite(d) or not d > 0:
            raise HintsError(f"{where}.duration_min: must be a finite number > 0")
        hint["duration_min"] = d
    if raw.get("title"):
        if not isinstance(raw["title"], str):
            raise HintsError(f"{where}.title: expected a string")
        if len(raw["title"]) > MAX_TEXT_LEN:
            raise HintsError(f"{where}.title: longer than {MAX_TEXT_LEN} characters")
        hint["title"] = raw["title"].strip()
    if raw.get("refresh") is not None:
        if not isinstance(raw["refresh"], bool):
            raise HintsError(f"{where}.refresh: must be true or false")
        hint["refresh"] = raw["refresh"]
    if raw.get("authors"):
        a = raw["authors"]
        if isinstance(a, str):
            a = [a]
        if not isinstance(a, list) or not all(isinstance(x, str) for x in a):
            raise HintsError(f"{where}.authors: expected a string or a list of strings")
        if len(a) > MAX_AUTHORS or any(len(x) > MAX_TEXT_LEN for x in a):
            raise HintsError(f"{where}.authors: at most {MAX_AUTHORS} names of {MAX_TEXT_LEN} characters")
        hint["authors"] = [x.strip() for x in a if x.strip()]
    return hint


def loadHintsFile(path):
    """Parse a hints file into {key: hint}. Raises HintsError on any structural problem (fail loudly: a
    silently ignored hint would look like a matching failure)."""
    if os.path.exists(path) and not os.path.isfile(path):
        raise HintsError(f"{path}: not a regular file")             # a FIFO here would block open() forever
    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise HintsError(f"{path}: larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise HintsError(f"{path}: top level must be an object mapping release keys to hints")
    hints = {}
    for key, raw in data.items():
        if not isinstance(key, str) or not key.strip():
            raise HintsError(f"{path}: empty hint key")
        if key.startswith("/"):
            key = os.path.normpath(key)          # a trailing slash on a path key must not stop it from matching
        if key in hints:
            raise HintsError(f"{path}: duplicate hint key {key!r} (after path normalisation)")
        hints[key] = normalizeHint(raw, f"{path}[{key!r}]")
    return hints


def _names(value):
    """A config list that may also be given as a single string; blanks dropped."""
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(v) for v in value if str(v).strip()]


def _hintKey(name):
    return os.path.normpath(name) if str(name).startswith("/") else str(name)


def parsePins(values):
    """`--pin RELEASE=ASIN` entries (Config/pins) as {release key: ASIN}. Raises HintsError on a malformed entry;
    a pin that cannot be applied must stop the run, not silently fall back to fuzzy matching."""
    pins = {}
    for raw in _names(values):
        name, sep, asin = str(raw).rpartition("=")
        if not sep or not name.strip():
            raise HintsError(f"--pin {raw!r}: expected RELEASE=ASIN (the release folder/file name or path, then the Audible ASIN)")
        key = _hintKey(name.strip())
        asin = _asin(asin, f"--pin {name.strip()!r}")
        if key in pins and pins[key] != asin:
            raise HintsError(f"--pin {name.strip()!r} given twice with different ASINs")
        pins[key] = asin
    return pins


def getHints(cfg):
    """Hints for this run ({} when none configured). Loaded once per file path. `--refresh NAME` entries
    (Config/refresh, a list of release names or paths) become {"refresh": true} hints; `--pin NAME=ASIN` entries
    (Config/pins) become {"asin": ASIN, "refresh": true}: the operator is correcting a match, so the cached
    answers and the processed marker for that release are ignored. A pin overrides an `asin` from the file."""
    path = cfg.get("Config/hints_file")
    refresh = _names(cfg.get("Config/refresh"))
    pins = _names(cfg.get("Config/pins"))
    key = (path, tuple(refresh), tuple(pins))
    if key not in _cache:
        hints = {}
        if path:
            if not os.path.exists(path):
                raise HintsError(f"hints file not found: {path}")
            hints = loadHintsFile(path)
            print(f"Loaded {len(hints)} hint(s) from {path}")
        for name in refresh:
            hints.setdefault(_hintKey(name), {})["refresh"] = True
        for k, asin in parsePins(pins).items():
            hint = hints.setdefault(k, {})
            hint["asin"] = asin
            hint["refresh"] = True
        _cache[key] = hints
    return _cache[key]


_appliedRefresh = set()
_acceptedPins = {}              # hint key -> ASIN, for pins whose Audible product was actually accepted this run


def noteRefreshApplied(hintKey):
    _appliedRefresh.add(hintKey)


def notePinAccepted(hintKey, asin):
    """Called when a pinned ASIN's Audible product was accepted as the match; only such pins are remembered."""
    if hintKey:
        _acceptedPins[hintKey] = str(asin).strip().upper()


def rememberEnabled(cfg):
    return bool(cfg.get("Config/remember_pins")) and bool(_names(cfg.get("Config/pins")))


def validateRemember(cfg):
    """A problem with --remember worth stopping for before the run, or None: it needs a hints file path, and an
    existing file must be one we can read back (rewriting a file we cannot parse would destroy it)."""
    if not rememberEnabled(cfg):
        return None
    path = cfg.get("Config/hints_file")
    if not path:
        return "--remember needs a hints file to write to: set Config/hints_file or pass --hints PATH"
    if os.path.exists(path) and not os.path.isfile(path):
        return f"--remember: {path} is not a regular file"
    if os.path.exists(path):
        loadHintsFile(path)             # raises HintsError / ValueError / OSError, reported by the caller (getHints has usually
    return None                         # already parsed it; this keeps the guarantee when validateRemember is called on its own)


def rememberPins(cfg):
    """After the run: write every pin whose Audible product was accepted for a release into the hints file as
    {"asin": ASIN} (no `refresh`: the correction must apply, not re-process the book on every run). A pin that
    matched a release but whose ASIN Audible did not return is not remembered: the processed marker would then hide
    the still-wrong book behind a hint that looks like a saved correction. Other entries and other fields of the
    same entry are kept; the file is rewritten atomically. Returns the number of pins written; never raises."""
    if not rememberEnabled(cfg):
        return 0
    path = str(cfg.get("Config/hints_file") or "")
    try:
        pins = {}
        for k, a in parsePins(cfg.get("Config/pins")).items():
            if _acceptedPins.get(k) == a:
                pins[k] = a
            elif k in _appliedRefresh:
                print(f"Not remembering --pin {k!r}: Audible did not return a usable product for {a}")
        if not path or not pins:
            return 0
        if cfg.get("Config/flags/dry_run"):
            print(f"[Dry Run] : would remember {len(pins)} pin(s) in {path}")
            return 0
        path = os.path.realpath(path)       # a symlinked hints file: update the target, do not replace the link
        data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                print(f"Not remembering pins: {path} is not a JSON object")
                return 0
        # match existing keys the way loadHintsFile normalises them, so a pin updates its entry instead of adding a twin
        existing = {(os.path.normpath(k) if k.startswith("/") else k): k for k in data if isinstance(k, str)}
        written = 0
        for key, asin in pins.items():
            entry_key = existing.get(key, key)
            entry = data.get(entry_key)
            if not isinstance(entry, dict):
                entry = {}
            if entry.get("asin") == asin and "refresh" not in entry:
                continue
            entry["asin"] = asin
            entry.pop("refresh", None)
            data[entry_key] = entry
            written += 1
        if not written:
            return 0
        for k, v in data.items():                                   # never write what we cannot read back
            normalizeHint(v, f"{path}[{k!r}]")
        directory = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
                if os.path.exists(path):
                    st = os.stat(path)
                    for keep in (lambda: os.fchmod(fd, st.st_mode & 0o777),          # keep the operator's mode and owner (root runs
                                 lambda: os.fchown(fd, st.st_uid, st.st_gid)):      # must not leave a hand-edited file root-owned)
                        try:
                            keep()
                        except OSError:
                            pass                        # network mounts refuse chmod, non-root cannot chown: a lost bit beats a lost correction
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        print(f"Remembered {written} pin(s) in {path}")
        return written
    except Exception as e:                  # noqa: BLE001 - a failed write must not change the outcome of the run
        print(f"Could not remember pins in {path}: {type(e).__name__}: {e}")
        return 0


def warnUnusedRefresh(cfg):
    """After a run: a --refresh or --pin name that matched no release is a typo the operator should hear about."""
    for name in _names(cfg.get("Config/refresh")):
        if _hintKey(name) not in _appliedRefresh:
            print(f"Warning: --refresh {name!r} matched no release in this run")
    try:
        pins = parsePins(cfg.get("Config/pins"))
    except HintsError:
        pins = {}
    for k in pins:
        if k not in _appliedRefresh:
            print(f"Warning: --pin {k!r} matched no release in this run")


def findHint(hints, name, paths=(), root=None):
    """The hint for a release (see findHintKey), or None when there is none."""
    key = findHintKey(hints, name, paths, root)
    return None if key is None else _found(hints, key)


def findHintKey(hints, name, paths=(), root=None):
    """The key of the hint for a release: exact match on its booktree name, then on the full path of any of its
    files, then on any ancestor folder of those files below `root` (the source path), so a hint keyed by the release
    folder also covers files in cd1/, cd2/ sub-folders. Returns None when there is none."""
    if not hints:
        return None
    if name in hints:
        return name
    for p in paths:
        if p in hints:
            return p
    #ancestor walk only with a known, comparable source root: without one a hint keyed by a top-level folder
    #such as "/data" would apply to every release
    if not root:
        return None
    root = os.path.normpath(root)
    for p in paths:
        if os.path.isabs(p) != os.path.isabs(root) or not os.path.normpath(p).startswith(root + os.sep):
            continue
        parent = os.path.dirname(os.path.normpath(p))
        while parent and parent not in ("/", ".", root):
            if parent in hints:
                return parent
            nxt = os.path.dirname(parent)
            if nxt == parent:
                break
            parent = nxt
    return None


def _found(hints, key):
    if hints[key].get("refresh"):
        noteRefreshApplied(key)
    return hints[key]


def durationDelta(expected_min, candidate_min):
    """Absolute difference in minutes, or None when either side is unknown/zero."""
    try:
        e, c = float(expected_min or 0), float(candidate_min or 0)
    except (TypeError, ValueError):
        return None
    if e <= 0 or c <= 0:
        return None
    return abs(e - c)


def withinTolerance(delta, tolerance=DURATION_TOLERANCE_MIN):
    return delta is not None and delta <= tolerance


def pickBest(scored, minMatchRate, requireRate=True, tolerance=DURATION_TOLERANCE_MIN):
    """Choose among (book, rate, delta) tuples.

    Eligible: rate >= minMatchRate (always eligible when requireRate is False and the runtime is within
    tolerance, used for caller-supplied candidates). Among eligible candidates those within the duration
    tolerance win; ties on the preferred set are broken by the higher fuzzy rate, then the smaller delta, then
    order (which reproduces upstream's "first highest score" behaviour when no duration evidence exists)."""
    eligible = [s for s in scored if s[1] >= minMatchRate or (not requireRate and withinTolerance(s[2], tolerance))]
    if not eligible:
        return None
    near = [s for s in eligible if withinTolerance(s[2], tolerance)]
    pool = near or eligible
    return max(pool, key=lambda s: (s[1], -(s[2] if s[2] is not None else float("inf"))))
