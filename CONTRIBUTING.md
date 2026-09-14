# Contributing

Thanks for helping. A few rules keep this fork safe to run unattended on other people's libraries.

**Compatibility.** The run-log CSV columns and order, the `True`/`False` strings, the log file name pattern and the
stdout phrases listed in `docs/FORK.md` are a public interface. Add columns or flags; do not rename or reorder.

**Never write under the source path.** Downloads are often live torrents. No moves, renames, tag rewrites, or files
created next to the sources. Hardlink/copy into the media path only. `fixid3` stays off by default.

**External services.** MAM calls are bounded per run and spaced ≥ 6 s; Audible/MAM results are cached with a TTL for
empty results; errors are never cached. Any new call site must go through the existing throttled helpers.

**Secrets.** Never commit configs, logs, caches or cookie files (`.gitignore` covers them). Tests use synthetic
fixtures only. CI runs gitleaks and push protection is on.

**Tests.** `python3 -m unittest discover -s tests -p 'test_*.py'` must pass. Behaviour changes to matching or output
need a replay comparison (`tests/replay/`) and the numbers in the PR description.

**Style.** `ruff check .` clean. Small, single-purpose commits; explain *why* in the message.
