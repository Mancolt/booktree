"""The library side of a run: telling Audiobookshelf to scan after new hardlinks, and not hardlinking a book
that is already in a library (`Config/dedupe_roots`).

    "abs": {
        "url": "http://audiobookshelf:13378",     # empty = off
        "library_id": "ef56....",                 # the ABS library to scan
        "scan": 1                                 # POST /api/libraries/<id>/scan when the run hardlinked something
    },
    "dedupe_roots": ["/media/audiobooks"]        # directories whose media files count as "already filed"

The ABS API token comes from the environment (`ABS_API_TOKEN`) or the first line of the file named by
`ABS_API_TOKEN_FILE`; it is never printed.

Dedupe: two folders hold the same book when their media files are the same inodes (hardlinks), which is
exactly how booktree files a download. Before hardlinking a matched book its source files are looked up in an
index of (device, inode) built once per run from the dedupe roots; a book already present is reported and left
alone, nothing is ever deleted. A book counts as filed only when every one of its files is (a filing that was
interrupted half-way is completed, as hardlinkFile skips files that already exist). A root that contains, or lies
inside, a source path is refused: the downloads themselves would then count as "already filed" and nothing under
it would ever be hardlinked.
"""
import os
import re
import stat

import requests

TIMEOUT = 30
LIBRARY_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")      # ABS ids are `lib_...` or UUIDs; anything else would change the URL path
MEDIA_EXTS = (".m4b", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma")

_index = None                   # {(st_dev, st_ino): folder} over the dedupe roots, built on first use
_indexRoots = None


def reset():
    global _index, _indexRoots
    _index = None
    _indexRoots = None


# ---------------------------------------------------------------- dedupe roots

def dedupeRoots(cfg):
    roots = cfg.get("Config/dedupe_roots") or []
    if isinstance(roots, str):
        roots = [roots]
    return [str(r) for r in roots if str(r).strip()]


def validateDedupeRoots(cfg):
    """A configuration problem with Config/dedupe_roots, or None."""
    roots = cfg.get("Config/dedupe_roots")
    if roots is None or roots == []:
        return None
    if not isinstance(roots, (list, str)):
        return "Config/dedupe_roots must be a list of directories"
    sources = []
    for entry in cfg.get("Config/paths") or []:
        if isinstance(entry, dict) and entry.get("source_path"):
            sources.append(os.path.realpath(str(entry["source_path"])))
    for root in dedupeRoots(cfg):
        if not os.path.isdir(root):
            return f"Config/dedupe_roots: {root} is not a directory"
        real = os.path.realpath(root)
        for src in sources:
            if src == real or src.startswith(real + os.sep):
                return (f"Config/dedupe_roots: {root} contains the source path {src}; the downloads themselves would "
                        f"count as already filed")
            if real.startswith(src + os.sep):
                return (f"Config/dedupe_roots: {root} lies inside the source path {src}; the downloads themselves would "
                        f"count as already filed")
    return None


def _buildIndex(roots):
    index = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if not name.lower().endswith(MEDIA_EXTS):
                    continue
                try:
                    st = os.lstat(os.path.join(dirpath, name))     # a symlink in the library is not a filed copy
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    index.setdefault((st.st_dev, st.st_ino), dirpath)
    return index


def alreadyFiled(cfg, files):
    """The library folder that already holds every one of `files` (paths of a book's media files), or None."""
    global _index, _indexRoots
    roots = dedupeRoots(cfg)
    if not roots:
        return None
    if _index is None or _indexRoots != roots:
        _index = _buildIndex(roots)
        _indexRoots = roots
        print(f"Indexed {len(_index)} media files under {len(roots)} dedupe root(s)")
    folders = []
    for path in files:
        try:
            st = os.stat(path)
        except OSError:
            return None
        folder = _index.get((st.st_dev, st.st_ino))
        if not folder:
            return None
        folders.append(folder)
    return folders[0] if folders else None


# ---------------------------------------------------------------- Audiobookshelf

def _flag(value, default=True):
    """Config flags arrive as 0/1, true/false or, from a hand-edited file, the strings "0"/"false"."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def absSettings(cfg):
    url = str(cfg.get("Config/abs/url") or "").strip().rstrip("/")
    library = str(cfg.get("Config/abs/library_id") or "").strip()
    scan = _flag(cfg.get("Config/abs/scan"), True)
    return url, library, scan


def absToken():
    token = os.environ.get("ABS_API_TOKEN", "").strip()
    if token:
        return token
    path = os.environ.get("ABS_API_TOKEN_FILE", "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        return line.strip()
        except (OSError, UnicodeDecodeError) as e:
            print(f"Could not read ABS_API_TOKEN_FILE {path}: {type(e).__name__}")
    return ""


def validateAbs(cfg):
    """A configuration problem with Config/abs, or None."""
    url, library, scan = absSettings(cfg)
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        return "Config/abs/url must be an http(s) URL"
    if scan and not library:
        return "Config/abs/library_id is required when Config/abs/url is set"
    if scan and not LIBRARY_ID.fullmatch(library):
        return "Config/abs/library_id may only contain letters, digits, - and _"
    if scan and not absToken():
        return "Audiobookshelf scan is configured but no API token was found (set ABS_API_TOKEN or ABS_API_TOKEN_FILE)"
    return None


def triggerAbsScan(cfg, hardlinked_files, dry_run=False, matched=0):
    """Ask Audiobookshelf to scan the library after a run that created hardlinks. Best effort: never raises.
    Returns True when the scan was accepted."""
    url, library, scan = absSettings(cfg)
    if not url or not scan or not library:
        return False
    if dry_run:
        if matched:
            print(f"[Dry Run] : would trigger an Audiobookshelf library scan after hardlinking {matched} matched book(s)")
        return False
    if not hardlinked_files:
        return False
    try:
        #no redirects: the request carries the token and a 3xx would turn the POST into a GET elsewhere
        r = requests.post(f"{url}/api/libraries/{library}/scan", headers={"Authorization": f"Bearer {absToken()}"},
                          timeout=TIMEOUT, allow_redirects=False)
        if 200 <= r.status_code < 300:
            print(f"Triggered an Audiobookshelf scan of library {library} (status {r.status_code})")
            return True
        print(f"Audiobookshelf refused the scan request for library {library} (status {r.status_code})")
    except requests.RequestException as e:
        print(f"Audiobookshelf scan request failed: {type(e).__name__}")
    except Exception as e:                  # noqa: BLE001 - never fail the run over a scan trigger
        print(f"Audiobookshelf scan skipped: {type(e).__name__}")
    return False
