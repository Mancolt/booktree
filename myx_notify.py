"""End-of-run notifications (`Config/notify`), so a hook or timer no longer needs a wrapper script to tell the
operator how the run went.

    "notify": {
        "ntfy_url": "https://ntfy.sh/your-topic",     # POST the summary here; empty = off
        "on": "unmatched",                             # always | unmatched (unmatched books or a failure) | failure
        "heartbeat_url": ""                            # GET after a clean run (Uptime Kuma push URL, healthchecks.io, ...)
    }

An ntfy access token comes from the environment (`NTFY_TOKEN`) or `Config/notify/ntfy_token`, never from the
message. Everything here is best effort: a dead notification endpoint must never change the outcome or the exit
code of a run, so failures are printed and swallowed. Requests are short (TIMEOUT) and never retried. The URLs
are not printed: an ntfy topic name and a push URL's token are what lets someone else post to them.
"""
import os
import re

import requests

TIMEOUT = 10
MAX_LISTED = 10
MAX_NAME = 120                  # release names are attacker-chosen torrent names: bounded and stripped of control chars
MAX_ERROR = 200
MAX_BODY = 3500                 # ntfy's message limit is 4096 bytes; above it the alert becomes an attachment or a 413
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MODES = ("always", "unmatched", "failure")


def settings(cfg):
    """(ntfy_url, mode, heartbeat_url, token); ntfy_url/heartbeat_url empty when not configured."""
    url = str(cfg.get("Config/notify/ntfy_url") or "").strip()
    heartbeat = str(cfg.get("Config/notify/heartbeat_url") or "").strip()
    mode = str(cfg.get("Config/notify/on") or "unmatched").strip().lower()
    token = os.environ.get("NTFY_TOKEN") or str(cfg.get("Config/notify/ntfy_token") or "")
    return url, mode, heartbeat, token.strip()


def validate(cfg):
    """A problem in Config/notify worth stopping for (a typo here would silently mute every alert), or None."""
    url, mode, heartbeat, _ = settings(cfg)
    if mode not in MODES:
        return f"Config/notify/on must be one of {', '.join(MODES)} (got {mode!r})"
    for name, value in (("ntfy_url", url), ("heartbeat_url", heartbeat)):
        if value and not value.lower().startswith(("http://", "https://")):
            return f"Config/notify/{name} must be an http(s) URL"
    return None


def shouldNotify(mode, unmatched, exit_code):
    if mode == "always":
        return True
    if mode == "failure":
        return exit_code != 0
    return exit_code != 0 or unmatched > 0


def clean(text, limit):
    text = CONTROL.sub(" ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def compose(summary, exit_code):
    """(title, priority, body) for the run. `summary` carries books/matched/unmatched/hardlinked_files/
    unmatched_names/csv/dry_run as built by booktree.py. Names, error text and the whole body are bounded."""
    books = summary.get("books", 0)
    matched = summary.get("matched", 0)
    unmatched = summary.get("unmatched", 0)
    dry = " (dry run)" if summary.get("dry_run") else ""
    if exit_code != 0:
        title, priority = f"booktree run failed (exit {exit_code})", "high"
        lines = [f"exit code {exit_code}: {clean(summary.get('error') or 'see the log', MAX_ERROR)}{dry}"]
        if books:
            lines.append(f"{matched}/{books} matched before the failure")
    elif unmatched:
        title, priority = "booktree: books need review", "default"
        lines = [f"{matched}/{books} matched, {unmatched} unmatched{dry}"]
    else:
        title, priority = "booktree run complete", "low"
        lines = [f"all {books} books matched{dry}" if books else f"nothing to process{dry}"]
    if summary.get("hardlinked_files"):
        lines.append(f"{summary['hardlinked_files']} file(s) hardlinked")
    names = summary.get("unmatched_names") or []
    if names:
        lines.append("unmatched:")
        lines.extend(f"  {clean(n, MAX_NAME)}" for n in names[:MAX_LISTED])
        if len(names) > MAX_LISTED:
            lines.append(f"  ... and {len(names) - MAX_LISTED} more")
    if summary.get("csv"):
        lines.append(f"log: {summary['csv']}")
    body = "\n".join(lines)
    while len(body.encode("utf-8")) > MAX_BODY:
        idx = max((i for i, l in enumerate(lines) if l.startswith("  ")), default=-1)
        if idx < 0:
            body = body.encode("utf-8")[:MAX_BODY].decode("utf-8", "ignore")
            break
        lines.pop(idx)                                        # drop listed names from the end; keep the counts and the log path
        body = "\n".join(lines)
    return title, priority, body


def send(cfg, summary, exit_code):
    """Post the summary to ntfy (when configured and the mode says so) and hit the heartbeat URL after a clean
    run. Returns (ntfy_sent, heartbeat_sent). Never raises."""
    ntfy_sent = heartbeat_sent = False
    try:
        url, mode, heartbeat, token = settings(cfg)
        if url and shouldNotify(mode, summary.get("unmatched", 0), exit_code):
            title, priority, body = compose(summary, exit_code)
            headers = {"Title": title, "Priority": priority, "Tags": "books"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                #no redirects: the request may carry a token and a 3xx would resend it as a GET elsewhere
                r = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=TIMEOUT, allow_redirects=False)
                if 200 <= r.status_code < 300:
                    ntfy_sent = True
                    print("Notification sent (ntfy)")
                else:
                    print(f"Notification refused by the ntfy server (status {r.status_code})")
            except requests.RequestException as e:
                print(f"Notification could not be sent: {type(e).__name__}")
        if heartbeat and exit_code == 0:
            try:
                r = requests.get(heartbeat, timeout=TIMEOUT, allow_redirects=False)
                heartbeat_sent = 200 <= r.status_code < 300
                if not heartbeat_sent:
                    print(f"Heartbeat refused (status {r.status_code})")
            except requests.RequestException as e:
                print(f"Heartbeat could not be sent: {type(e).__name__}")
    except Exception as e:                  # noqa: BLE001 - a notification must never fail the run
        print(f"Notification skipped: {type(e).__name__}")
    return ntfy_sent, heartbeat_sent
