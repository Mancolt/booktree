#!/usr/bin/env python3
"""Offline regression replay of booktree over the historical run-log corpus.

For every historical run log (booktree_log_*.csv) the rows are regrouped into books exactly as
buildTreeFromLog does (one book per (row-index, book) key), the id3 Book is rebuilt from the id3-*
columns, and the REAL booktree code paths are executed with the network disabled:

  * MAMBook.getAudibleBooks  -> search-key construction (getAltTitle, cleanseTitle, optimizeKeys),
                                cache-key hashing, ranking of the cached Audible response
  * MAMBook.getMAMBooks      -> MAM search-string construction, cache-key hashing, ranking of the
                                cached MAM response (snatched-only filter)
  * BookFile.getConfigTargetPath -> the hardlink target ("paths" column) for the logged best match
  * myx_utilities.createOPF  -> the metadata.opf text for the logged best match
  * every stdout line the above print (the phrases downstream consumers scrape)

Nothing is written outside --out.  Cache and logs are only read.  Output:
  <out>/books.jsonl   one record per (run, book) with keys, hashes, replayed vs logged results
  <out>/stdout/<run>/<bookhash>.txt   the captured stdout transcript per book
  <out>/opf/<run>/<bookhash>.opf      the generated OPF per matched book
  <out>/summary.json  aggregate counts (this is what a later run is compared against)

Run it inside the booktree image with the repo mounted over /booktree (see run_baseline.sh).
"""
import argparse, contextlib, csv, glob, hashlib, io, json, os, sys, tempfile, time
from types import SimpleNamespace

sys.path.insert(0, os.getcwd())          # booktree modules live in the cwd (/booktree)
import myx_args, myx_classes, myx_utilities, myx_mam   # noqa: E402


# ---------------------------------------------------------------- network kill-switch
class _Offline(Exception):
    pass


class OfflineClient:
    """Stands in for httpx: any request raises, so getAudibleBook falls through to its except branch
    (prints 'Error searching audible: ...') and never calls cacheMe."""
    def get(self, *a, **k):
        raise _Offline("offline replay: network disabled")


class _OfflineSession:
    def __init__(self):
        self.headers = {}
        self.cookies = None
    def get(self, *a, **k):
        raise _Offline("offline replay: network disabled")
    def post(self, *a, **k):
        raise _Offline("offline replay: network disabled")


myx_mam.requests = SimpleNamespace(Session=_OfflineSession)   # searchMAM builds requests.Session()


# ---------------------------------------------------------------- helpers
def load_cfg(path, cache_path, log_path):
    params = SimpleNamespace(config_file=path, dry_run=None, verbose=True, no_cache=None, no_opf=None,
                             multibook=None, ebooks=None, fixid3=None, add_narrators=None, hints=None)
    cfg = myx_args.Config(params)
    cfg._data["Config"]["cache_path"] = cache_path
    cfg._data["Config"]["log_path"] = log_path
    cfg._data["Config"]["session"] = ""            # never needed offline; never log it
    return cfg


def book_from_row(row, ns):
    b = myx_classes.Book(asin=str(row[f"{ns}asin"]), title=str(row[f"{ns}title"]), subtitle=row[f"{ns}subtitle"],
                         publisher=row[f"{ns}publisher"], length=row[f"{ns}length"], duration=row[f"{ns}duration"],
                         language=row[f"{ns}language"] or "english")
    b.setAuthors(row[f"{ns}authors"] or "")
    b.setNarrators(row[f"{ns}narrators"] or "")
    set_series_from_log(b, row[f"{ns}series"] or "", row[f"{ns}seriesparts"] or "")
    return b


def set_series_from_log(book, series_col, seriesparts_col):
    """Rebuild Series objects from the log columns.

    The CSV writes `series` = getSeries() (names, comma-joined) and `seriesparts` = getSeriesParts()
    = "<name> <separator><part>" where the separator is "" for Audible/MAM books, i.e. "The Witcher 1".
    Upstream's log-mode reader (Book.setSeries) splits on '#', so it turns that into
    Series(name="The Witcher 1", part="") -- the origin of the `Series # - Title` folders (upstream
    issue #27 in log mode).  The replay wants the metadata as the ORIGINAL hybrid run saw it, so we
    strip the known series name off the front of the seriesparts value to recover the part.
    Falls back to upstream's setSeries when the columns cannot be aligned."""
    names = [n.strip() for n in series_col.split(",") if n.strip()]
    parts = seriesparts_col.strip()
    # seriesparts went through cleanseAuthor (dots removed, whitespace collapsed); series through cleanseSeries
    key = myx_utilities.cleanseAuthor(names[0]) if len(names) == 1 else None
    if key is not None and parts.startswith(key):
        book.series.append(myx_classes.Series(names[0], parts[len(key):].strip().lstrip("#").strip()))
    elif not names and parts:
        book.setSeries(parts)
    elif names:
        # multiple series: best effort, positional split is ambiguous -> keep upstream behaviour
        book.setSeries(parts)


def sha(s):
    return hashlib.sha256(s.encode("utf-8", "surrogatepass")).hexdigest()


def group_run(path):
    """Regroup one log exactly like buildTreeFromLog: key = hash(f'{i}-{book}') with i the 1-based row index."""
    books = {}
    order = []
    with open(path, newline="", errors="ignore", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, fieldnames=list(myx_utilities.getLogHeaders().keys()))
        i = 1
        for row in reader:
            if i > 1:
                f = str(row["file"])
                bf = myx_classes.BookFile(f, f, str(row["sourcePath"]), str(row["mediaPath"]),
                                          isHardlinked=(str(row["isHardLinked"]).lower() == "true"))
                bf.ffprobeBook = book_from_row(row, "id3-")
                bf.isMatched = (str(row["isMatched"]).lower() == "true")
                # NB: upstream keys on the row index, so every row is its own MAMBook in log mode; for a
                # faithful replay of the hybrid run that produced the log we regroup by the 'book' key
                # (folder or filename), which is what buildTreeFromHybridSources did.
                key = str(row["book"])
                if key not in books:
                    mb = myx_classes.MAMBook(key)
                    mb.metadata = str(row["metadatasource"])
                    mb.paths = str(row["paths"])
                    mb.isMatched = bf.isMatched
                    mb.ffprobeBook = bf.ffprobeBook
                    mb._rows = []
                    books[key] = mb
                    order.append(key)
                books[key].files.append(bf)
                books[key]._rows.append(row)
            i += 1
    return [books[k] for k in order]


def replay_book(mb, cfgs, out_dirs, run):
    rows = mb._rows
    r0 = rows[0]
    rec = {
        "run": run, "book": mb.name, "book_hash": sha(mb.name), "files": len(rows),
        "logged": {
            "isMatched": r0["isMatched"], "isHardLinked": [r["isHardLinked"] for r in rows],
            "metadatasource": r0["metadatasource"], "mamCount": r0["mamCount"],
            "audibleMatchCount": r0["audibleMatchCount"], "adb_asin": r0["adb-asin"], "mam_asin": r0["mam-asin"],
            "adb_matchRate": r0["adb-matchRate"], "mam_matchRate": r0["mam-matchRate"], "paths": r0["paths"],
        },
        "id3": {"asin": r0["id3-asin"], "title": r0["id3-title"], "authors": r0["id3-authors"],
                "narrators": r0["id3-narrators"], "duration": r0["id3-duration"]},
        "passes": {},
    }
    for tag, cfg in cfgs.items():
        buf = io.StringIO()
        # fresh MAMBook per pass: getAudibleBooks mutates book.title via getAltTitle
        b = myx_classes.MAMBook(mb.name)
        b.metadata = "id3"
        for bf in mb.files:
            nbf = myx_classes.BookFile(bf.file, bf.fullPath, bf.sourcePath, bf.mediaPath)
            nbf.ffprobeBook = book_from_row(next(r for r in rows if r["file"] == bf.file), "id3-")
            b.files.append(nbf)
        b.ffprobeBook = b.files[0].ffprobeBook
        metadata = cfg.get("Config/metadata")
        p = {"metadata": metadata}
        with contextlib.redirect_stdout(buf):
            print(f"Processing: {b.name}...")
            try:
                if "mam" in metadata:
                    b.getMAMBooks(cfg, b.files[0])
                    if b.bestMAMMatch is not None:
                        b.metadata = "mam"
                if "audible" in metadata:
                    src = b.bestMAMMatch if (b.bestMAMMatch is not None and b.bestMAMMatch.language.lower() != "english") else None
                    if metadata == "mam-audible" and src is None and not myx_utilities.isMultiBookCollection(b.files[0].file):
                        src = b.bestMAMMatch
                    if src is None:
                        src = b.ffprobeBook
                    b.getAudibleBooks(OfflineClient(), src, cfg)
                    if b.bestAudibleMatch is not None:
                        b.metadata = "audible"
                print(f"Found {len(b.mamMatches)} MAM matches, {len(b.audibleMatches)} Audible Matches")
                myx_utilities.printDivider()
            except _Offline:
                pass
            except Exception as e:      # replay must never abort on one book
                print(f"REPLAY-EXCEPTION {type(e).__name__}: {e}")
                p["exception"] = f"{type(e).__name__}: {e}"
        text = buf.getvalue()
        cache_refs = [l.split("Checking cache: ", 1)[1].rstrip(".") for l in text.splitlines() if l.startswith("Checking cache: ")]
        hits = [l for l in text.splitlines() if l.startswith("Retrieving ")]
        p.update({
            "cache_keys": cache_refs,
            "audible_cache_hit": any(h.endswith("from audible") for h in hits),
            "mam_cache_hit": any(k.startswith("mam/") and os.path.exists(os.path.join(cfg.get("Config/cache_path"), "__cache__", k)) for k in cache_refs),
            "audibleMatches": len(b.audibleMatches or []), "mamMatches": len(b.mamMatches or []),
            "best_adb_asin": b.bestAudibleMatch.asin if b.bestAudibleMatch else "",
            "best_adb_rate": b.bestAudibleMatch.matchRate if b.bestAudibleMatch else "",
            "best_mam_asin": b.bestMAMMatch.asin if b.bestMAMMatch else "",
            "matchFound": b.matchFound(), "search_title_used": b.ffprobeBook.title,
            "stdout_sha": sha(text), "stdout_lines": len(text.splitlines()),
        })
        p["adb_agrees_with_log"] = (p["best_adb_asin"] == r0["adb-asin"]) if p["audible_cache_hit"] else None
        d = os.path.join(out_dirs["stdout"], run); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{rec['book_hash']}.{tag}.txt"), "w", encoding="utf-8", errors="surrogateescape") as fh:
            fh.write(text)
        rec["passes"][tag] = p

    # ---- deterministic parts from the LOGGED best match: target path + OPF
    cfg = next(iter(cfgs.values()))
    src = r0["metadatasource"]
    meta = None
    if src == "audible" and r0["adb-asin"]:
        meta = book_from_row(r0, "adb-")
    elif src == "mam" and (r0["mam-title"] or r0["mam-asin"]):
        meta = book_from_row(r0, "mam-")
    rec["target_path"] = {"logged": r0["paths"], "computed": None, "agree": None}
    rec["opf"] = None
    if meta is not None:
        try:
            computed = mb.files[0].getConfigTargetPath(cfg, meta)
            rec["target_path"].update(computed=computed, agree=(computed == r0["paths"]))
        except Exception as e:
            rec["target_path"]["computed"] = f"EXC {type(e).__name__}: {e}"
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            myx_utilities.createOPF(meta, td)
            opf_path = os.path.join(td, "metadata.opf")
            if os.path.exists(opf_path):
                opf = open(opf_path, encoding="utf-8").read()
                d = os.path.join(out_dirs["opf"], run); os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, f"{rec['book_hash']}.opf"), "w", encoding="utf-8") as fh:
                    fh.write(opf)
                rec["opf"] = {"sha": sha(opf), "bytes": len(opf),
                              "has_bare_amp": ("&" in opf and "&amp;" not in opf),
                              "well_formed": xml_ok(opf)}
    return rec


def xml_ok(text):
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(text.encode("utf-8"))
        return True
    except ET.ParseError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="/logs")
    ap.add_argument("--cache", default="/Config", help="dir that contains __cache__/")
    ap.add_argument("--config", action="append", required=True, help="tag=path, e.g. pass1=/Config/config.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    out_dirs = {k: os.path.join(a.out, k) for k in ("stdout", "opf")}
    for d in out_dirs.values():
        os.makedirs(d, exist_ok=True)
    cfgs = {}
    for spec in a.config:
        tag, path = spec.split("=", 1)
        cfgs[tag] = load_cfg(path, a.cache, a.logs)

    logs = sorted(glob.glob(os.path.join(a.logs, "booktree_log_*.csv")))
    if a.limit:
        logs = logs[-a.limit:]
    t0 = time.time()
    summary = {"runs": 0, "rows": 0, "books": 0, "logged_matched_books": 0, "logged_hardlinked_rows": 0,
               "passes": {t: {"audible_cache_hits": 0, "adb_agree": 0, "adb_disagree": 0, "mam_cache_hits": 0,
                              "matchFound": 0, "exceptions": 0} for t in cfgs},
               "target_path": {"compared": 0, "agree": 0}, "opf": {"generated": 0, "bare_amp": 0, "not_well_formed": 0},
               "stdout_sha_all": hashlib.sha256(), "opf_sha_all": hashlib.sha256()}
    with open(os.path.join(a.out, "books.jsonl"), "w", encoding="utf-8") as jl:
        for lp in logs:
            run = os.path.basename(lp)[len("booktree_log_"):-4]
            books = group_run(lp)
            summary["runs"] += 1
            for mb in books:
                rec = replay_book(mb, cfgs, out_dirs, run)
                summary["rows"] += rec["files"]; summary["books"] += 1
                summary["logged_matched_books"] += rec["logged"]["isMatched"] == "True"
                summary["logged_hardlinked_rows"] += sum(h == "True" for h in rec["logged"]["isHardLinked"])
                for t, p in rec["passes"].items():
                    s = summary["passes"][t]
                    s["audible_cache_hits"] += p["audible_cache_hit"]
                    s["mam_cache_hits"] += p["mam_cache_hit"]
                    s["matchFound"] += p["matchFound"]
                    s["exceptions"] += "exception" in p
                    if p["adb_agrees_with_log"] is True: s["adb_agree"] += 1
                    if p["adb_agrees_with_log"] is False: s["adb_disagree"] += 1
                    summary["stdout_sha_all"].update(p["stdout_sha"].encode())
                if rec["target_path"]["agree"] is not None:
                    summary["target_path"]["compared"] += 1
                    summary["target_path"]["agree"] += rec["target_path"]["agree"]
                if rec["opf"]:
                    summary["opf"]["generated"] += 1
                    summary["opf"]["bare_amp"] += rec["opf"]["has_bare_amp"]
                    summary["opf"]["not_well_formed"] += not rec["opf"]["well_formed"]
                    summary["opf_sha_all"].update(rec["opf"]["sha"].encode())
                jl.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"[{summary['runs']}/{len(logs)}] {run}: {len(books)} books", file=sys.stderr)
    summary["stdout_sha_all"] = summary["stdout_sha_all"].hexdigest()
    summary["opf_sha_all"] = summary["opf_sha_all"].hexdigest()
    summary["seconds"] = round(time.time() - t0, 1)
    with open(os.path.join(a.out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
