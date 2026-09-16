"""Additive structured run log (--json-log): one JSON object per line, one per processed book plus a final run
record. The CSV log and stdout phrases are unchanged; this exists so consumers can stop scraping them.

Record (type "book"), schema_version 1:
    run, release, release_path, files[{path, duration_s, hardlinked}], id3{asin,title,authors,narrators,duration_s (first file)},
    parsed_name{title,authors,series,part,asin} | null, hint | null, pinned_asin,
    matched, metadata_source, match{asin,title,authors,narrators,series,part,runtime_min,match_rate,attempt} | null,
    expected_duration_min, runtime_delta_min | null, target_path, hardlinked, mam_count, audible_count,
    queries[{kind, cache_key, cached, results, error?, skipped?, asin,title,authors,narrators,keywords | text}]
Record (type "run", one per Config/paths entry): run, started_utc, finished_utc, metadata, books, matched, unmatched,
    hardlinked_files, mam_queries (HTTP searches), mam_queries_skipped (budget), audible_queries, errors, csv, exit_code
"""
import json
import os
from datetime import datetime, timezone

SCHEMA_VERSION = 1
current = None          # the MAMBook being processed; search functions attach their queries to it


def begin(book):
    global current
    current = book
    if not hasattr(book, "queries") or book.queries is None:
        book.queries = []


def end():
    global current
    current = None


def noteQuery(kind, cache_key, cached, results, **fields):
    if current is None:
        return
    entry = {"kind": kind, "cache_key": cache_key, "cached": bool(cached), "results": results}
    entry.update({k: v for k, v in fields.items() if v not in ("", None)})
    current.queries.append(entry)


def _book(b):
    if b is None:
        return None
    return {"asin": b.asin, "title": b.title, "authors": [a.name for a in b.authors],
            "narrators": [n.name for n in b.narrators],
            "series": b.series[0].name if b.series else "", "part": str(b.series[0].part) if b.series else "",
            "runtime_min": b.length, "match_rate": b.matchRate}


def record(book, cfg, run_id):
    files = []
    for f in book.files:
        try:
            dur = float(f.ffprobeBook.duration or 0) if f.ffprobeBook else 0.0
        except (TypeError, ValueError):
            dur = 0.0
        files.append({"path": f.fullPath, "duration_s": dur, "hardlinked": bool(f.isHardlinked)})
    id3 = book.ffprobeBook
    best = book.bestAudibleMatch if book.metadata == "audible" else (book.bestMAMMatch if book.metadata == "mam" else None)
    match = _book(best) if book.isMatched and best is not None else None
    if match is not None:
        match["attempt"] = getattr(book, "matchAttempt", "") or ("mam" if book.metadata == "mam" else "log")
    expected = book.getExpectedDuration() if hasattr(book, "getExpectedDuration") else float(book.getRunTimeLength())
    delta = None
    if match and match.get("runtime_min") and expected:
        try:
            delta = round(abs(float(match["runtime_min"]) - float(expected)), 1)
        except (TypeError, ValueError):
            delta = None
    target = ""
    if book.files and best is not None:
        try:
            #log mode reuses the input row's target when the row was already matched (as createHardLinks does)
            if cfg.get("Config/metadata") == "log" and book.isMatched and getattr(book, "paths", ""):
                target = book.paths
            else:
                target = book.files[0].getConfigTargetPath(cfg, best) or ""
        except Exception:
            target = ""
    #hardlinked = every file of the release is present at the target (covers files linked by an earlier run)
    for f in files:
        if not f["hardlinked"] and target:
            try:
                from pathvalidate import sanitize_filename
                f["hardlinked"] = os.path.exists(os.path.join(target, sanitize_filename(os.path.basename(f["path"]))))
            except Exception:
                pass
    parsed = getattr(book, "parsedName", None)
    return {
        "type": "book", "schema_version": SCHEMA_VERSION, "run": run_id,
        "release": book.name,
        "release_path": os.path.dirname(book.files[0].fullPath) if book.files else "",
        "files": files,
        "id3": {"asin": id3.asin, "title": id3.title, "authors": [a.name for a in id3.authors],
                "narrators": [n.name for n in id3.narrators], "duration_s": files[0]["duration_s"] if files else 0.0} if id3 else None,
        "parsed_name": {k: parsed[k] for k in ("title", "authors", "series", "part", "asin")} if parsed else None,
        "hint": getattr(book, "hint", None),
        "pinned_asin": getattr(book, "pinnedAsin", "") or "",
        "matched": bool(book.isMatched),
        "metadata_source": book.metadata,
        "match": match,
        "expected_duration_min": expected,
        "runtime_delta_min": delta,
        "target_path": target,
        "hardlinked": bool(files) and all(f["hardlinked"] for f in files),
        "already_filed": getattr(book, "alreadyFiled", None) or None,       # Config/dedupe_roots: folder that holds the same files
        "mam_count": len(book.mamMatches or []),
        "audible_count": len(book.audibleMatches or []),
        "queries": getattr(book, "queries", []) or [],
    }


def runRecord(run_id, started, cfg, books, matched, hardlinked_files, csv_path, exit_code=0):
    q = [x for b in books for x in (getattr(b, "queries", []) or [])]
    return {
        "type": "run", "schema_version": SCHEMA_VERSION, "run": run_id,
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metadata": cfg.get("Config/metadata"),
        "books": len(books), "matched": matched, "unmatched": len(books) - matched,
        "hardlinked_files": hardlinked_files,
        "mam_queries": sum(1 for x in q if x["kind"] == "mam" and not x["cached"] and not x.get("skipped") and not x.get("error")),
        "audible_queries": sum(1 for x in q if x["kind"] == "audible" and not x["cached"] and not x.get("error")),
        "mam_queries_skipped": sum(1 for x in q if x["kind"] == "mam" and x.get("skipped")),
        "errors": sum(1 for x in q if x.get("error")),
        "csv": csv_path, "exit_code": exit_code,
    }


def openAppendNoFollow(path):
    """Open for appending without following a symlink at `path` (a planted link in the log dir must not redirect
    the write); parent directories are created."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o644)
    # backslashreplace: a file name that is not valid UTF-8 (surrogate-escaped by os.listdir) is written as \udcXX
    # escapes, valid JSON that round-trips to the same str; the file stays valid UTF-8 and the run never aborts
    return os.fdopen(fd, "a", encoding="utf-8", errors="backslashreplace", newline="")


def write(path, records):
    """Append records as JSON lines."""
    if not path or not records:
        return
    with openAppendNoFollow(path) as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
