"""Cache freshness policy for the Audible and MAM search caches.

Upstream cached every Audible answer forever, including empty ones, so a transient or malformed query became a
permanent miss (the owner ran a weekly prune script as a workaround), and it never cached empty MAM answers, so
every unmatched release queried MAM again on every run. Entries keep upstream's file format (the raw API JSON
under __cache__/<kind>/<sha256 of the query>); freshness is decided from the file's age and whether the answer
was empty:

    Config/cache/audible_positive_hours   default 720 (30 days: catalog data drifts, editions appear)
    Config/cache/audible_empty_hours      default 6
    Config/cache/mam_positive_hours       default 168 (7 days)
    Config/cache/mam_empty_hours          default 24

Errors are never cached (the callers only cache after a successful response). An Audible per-ASIN answer without
a title (the skeleton {asin, asset_details, is_vvab}) and a MAM answer without a snatched entry count as empty.
"""
import json
import os
import time

DEFAULT_HOURS = {
    ("audible", False): 720, ("audible", True): 6,
    ("mam", False): 168, ("mam", True): 24,
}


def isEmptyResult(category, content):
    """Does this cached API answer carry nothing usable?"""
    try:
        if category == "audible":
            if "product" in content:
                p = content.get("product") or {}
                return not p.get("title")
            products = content.get("products") or []
            return len([p for p in products if p.get("title")]) == 0
        if category == "mam":
            data = (content or {}).get("data") or []
            return not any(bool(d.get("my_snatched")) for d in data)
    except (AttributeError, TypeError):
        return True
    return False


def ttlHours(category, empty, cfg):
    key = f"Config/cache/{category}_{'empty' if empty else 'positive'}_hours"
    value = cfg.get(key)
    try:
        hours = float(value) if value is not None else DEFAULT_HOURS[(category, empty)]
    except (TypeError, ValueError):
        hours = DEFAULT_HOURS[(category, empty)]
    return hours


MAX_ENTRY_BYTES = 16 * 1024 * 1024      # a search answer is a few hundred KB at most; bigger = not ours
CLOCK_SKEW_SECONDS = 300


def entryState(path, category, cfg, now=None):
    """('missing'|'fresh'|'expired', age_hours, empty, ttl_hours) for a cache file.

    Unreadable, non-object, oversized entries and entries dated in the future (a planted file would never age
    out) are 'expired' and get refetched and overwritten."""
    if not os.path.exists(path):
        return "missing", 0.0, False, 0.0
    if category not in ("audible", "mam"):
        return "fresh", 0.0, False, float("inf")
    now = time.time() if now is None else now
    try:
        st = os.stat(path)
    except OSError:
        return "expired", 0.0, True, 0.0
    if st.st_mtime > now + CLOCK_SKEW_SECONDS or st.st_size > MAX_ENTRY_BYTES:
        return "expired", 0.0, True, 0.0
    age = max(0.0, (now - st.st_mtime) / 3600.0)
    shortest = min(ttlHours(category, True, cfg), ttlHours(category, False, cfg))
    if age <= shortest:
        return "fresh", age, False, shortest          # fresh whatever the answer holds: no need to parse it
    try:
        with open(path, encoding="utf-8") as fh:
            content = json.load(fh)
        if not isinstance(content, dict):
            return "expired", age, True, 0.0
        empty = isEmptyResult(category, content)
    except (OSError, ValueError):
        return "expired", age, True, 0.0
    ttl = ttlHours(category, empty, cfg)
    return ("fresh" if age <= ttl else "expired"), age, empty, ttl
