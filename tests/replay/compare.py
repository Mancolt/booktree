#!/usr/bin/env python3
"""Diff two replay outputs (books.jsonl) book by book.

usage: compare.py <baseline-dir> <candidate-dir> [--show N]
Reports, per pass, how many books changed: stdout transcript, best Audible ASIN, best MAM ASIN,
matchFound, cache keys (search-key construction), plus target path and OPF changes.  Exit 0 always;
the numbers are the verdict — a byte-compat step must show 0 changes everywhere, an A/B step shows
what it changed.
"""
import argparse, json, os


def load(d):
    out = {}
    with open(os.path.join(d, "books.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            out[(r["run"], r["book_hash"])] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("cand"); ap.add_argument("--show", type=int, default=10)
    a = ap.parse_args()
    A, B = load(a.base), load(a.cand)
    keys = sorted(set(A) | set(B))
    counts = {}
    examples = {}

    def bump(cat, key, detail):
        counts[cat] = counts.get(cat, 0) + 1
        examples.setdefault(cat, []).append((key, detail))

    for k in keys:
        ra, rb = A.get(k), B.get(k)
        if ra is None or rb is None:
            bump("books_only_in_one_side", k, "baseline" if rb is None else "candidate"); continue
        for tag in sorted(set(ra["passes"]) | set(rb["passes"])):
            pa, pb = ra["passes"].get(tag, {}), rb["passes"].get(tag, {})
            for field in ("stdout_sha", "best_adb_asin", "best_mam_asin", "matchFound", "cache_keys", "search_title_used", "audibleMatches", "mamMatches"):
                if pa.get(field) != pb.get(field):
                    bump(f"{tag}.{field}", k, f"{ra['book']!r}: {pa.get(field)!r} -> {pb.get(field)!r}" if field != "stdout_sha" else ra["book"])
            if pa.get("matchFound") != pb.get("matchFound"):
                bump(f"{tag}.match_" + ("gained" if pb.get("matchFound") else "lost"), k, ra["book"])
        if ra["target_path"].get("computed") != rb["target_path"].get("computed"):
            bump("target_path", k, f"{ra['target_path'].get('computed')!r} -> {rb['target_path'].get('computed')!r}")
        oa, ob = ra.get("opf") or {}, rb.get("opf") or {}
        if oa.get("sha") != ob.get("sha"):
            bump("opf_text", k, ra["book"])
        if oa.get("well_formed") != ob.get("well_formed"):
            bump("opf_well_formed_" + ("fixed" if ob.get("well_formed") else "broken"), k, ra["book"])
    print(f"books compared: {len(keys)}  (baseline {len(A)}, candidate {len(B)})")
    if not counts:
        print("NO DIFFERENCES"); return
    for cat in sorted(counts):
        print(f"\n== {cat}: {counts[cat]}")
        for key, detail in examples[cat][:a.show]:
            print(f"   {key[0]} {detail}")


if __name__ == "__main__":
    main()
