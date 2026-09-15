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


def noteRefreshApplied(hintKey):
    _appliedRefresh.add(hintKey)


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
    """The hint for a release: exact match on its booktree name, then on the full path of any of its files,
    then on any ancestor folder of those files below `root` (the source path), so a hint keyed by the release
    folder also covers files in cd1/, cd2/ sub-folders. Returns None when there is none."""
    if not hints:
        return None
    if name in hints:
        return _found(hints, name)
    for p in paths:
        if p in hints:
            return _found(hints, p)
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
                return _found(hints, parent)
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
