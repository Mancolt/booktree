# About this fork

A maintained fork of [myxdvz/booktree](https://github.com/myxdvz/booktree) (GPL-3.0). Upstream has been inactive since
March 2026 with open issues and PRs; this fork fixes the defects below while keeping booktree's outputs compatible
with existing consumers (Audiobookshelf, scripts that parse the run log). Image: `ghcr.io/mancolt/booktree`.

## Compatibility promise (v1 output contract)

Scripts built on upstream booktree keep working:

* Run log `booktree_log_YYYYMMDDHHMMSS.csv`: same 46 columns in the same order, one row per file, `isMatched` /
  `isHardLinked` as the strings `True` / `False`. New information is added as new columns or via `--json-log`, never by
  renaming or reordering.
* stdout phrases stay unchanged: `Processing: …`, `Searching Audible for` with indented `asin:` `title:` `authors:`
  `narrators:` `keywords:`, `Searching MAM for` with `TitleFilename:`, `Checking cache: <kind>/<sha256>`,
  `Caching <hash> in File: …`, `Found N Audible match(es)`, `Creating Hardlinks for N matched books`,
  `Hardlinking files for <title>` with `from` / `to`.
* Target layout `{author}/{title}` and `{author}/{series}/{series} #{part} - {title}` (configurable as before).
* Source files are never moved, renamed, re-tagged or written next to; hardlinking (or copying) into the media path is
  the only file operation, and `fixid3` stays off by default. Downloads are frequently seeded torrents.
* MAM is queried at most a bounded number of times per run with ≥ 6 s between calls.

## Defects addressed (roadmap)

| # | defect | upstream ref |
|---|---|---|
| 1 | `metadata.opf` text is not XML-escaped; a publisher such as `Little, Brown & Company` makes Audiobookshelf discard the whole OPF, ASIN included | — |
| 2 | With an explicit ASIN in log mode the id3 title/author still vetoes the match; no way to pass external hints or use duration as evidence | — |
| 3 | Missing id3 → the whole filename is the search title (`Author - Title.m4b` scores 41 against its own MAM entry) | #26 |
| 4 | Empty Audible / MAM results and skeleton per-ASIN responses (`{asin, asset_details, is_vvab}`) are cached forever | #25 |
| 5 | MAM session duplicated in every config file; `title_patterns` contain `"\b"` JSON escapes that become backspaces; exit code is 0 on failure | — |
| 6 | `Series # - Title` folders: the log writes `seriesparts` as `Name part`, the log reader splits on `#` | #27 |
| 7 | mousehole cookie integration (PR #24, updated for mousehole's v2 state file) | #24 |

## Regression replay

`tests/replay/` replays historical run logs offline inside the image: each book is rebuilt from its CSV rows, the
real search-key construction, Audible/MAM ranking (from cached API responses, network disabled), target path and OPF
generation are executed, and the stdout transcript is captured per book. `compare.py` diffs two runs; a
compatibility-preserving change must report `NO DIFFERENCES` for stdout, cache keys and target paths apart from the
change under test. `baseline-summary.json` is the frozen result for the upstream code (256 runs, 1,576 books:
target path reproduced for 261/266 matched books; 69/266 OPF files not well-formed; two consecutive runs identical).

```
BOOKTREE_LOGS=/path/to/logs BOOKTREE_CONFIG=/path/to/config tests/replay/run_replay.sh ghcr.io/mancolt/booktree:dev out/
python3 tests/replay/compare.py tests/replay/out/baseline out/
```
