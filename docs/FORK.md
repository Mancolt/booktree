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
| 1 | `metadata.opf` text is not XML-escaped; a publisher such as `Little, Brown & Company` makes Audiobookshelf discard the whole OPF, ASIN included. Fixed. | — |
| 2 | With an explicit ASIN in log mode the id3 title/author still vetoes the match; no way to pass external hints or use duration as evidence. Fixed: pinned ASIN is authoritative, `--hints` file, runtime within ±2 min preferred (see CONFIG.md) | — |
| 3 | Missing id3 → the whole filename is the search title (`Author - Title.m4b` scores 41 against its own MAM entry). Fixed: release-name parsing (`flags/parse_names`, `--legacy-names` for the old behaviour), see CONFIG.md | #26 |
| 4 | Empty Audible / MAM results and skeleton per-ASIN responses (`{asin, asset_details, is_vvab}`) are cached forever. Fixed: TTLs by kind and emptiness, errors never cached, `--refresh`, `--json-log`; MAM requests spaced ≥ 6 s and capped per run (see CONFIG.md) | #25 |
| 5 | MAM session duplicated in every config file and kept in a pickle; `title_patterns` contain `"\b"` JSON escapes that become backspaces; exit code is 0 on failure. Fixed: `MAM_SESSION` / `MAM_SESSION_FILE`, JSON cookie store, backspace patterns repaired, exit codes 0/1/2 (see CONFIG.md) | — |
| 6 | `Series # - Title` folders: the log writes `seriesparts` as `Name part`, the log reader splits on `#`. Fixed: the reader pairs `seriesparts` with `series`, decimal parts are logged as `17.5`, and a series entry without a part is filed with `target_path/in_series_no_part` (default `Series - Title`) | #27 |
| 6b | Multi-disc releases (`cd1/`, `Disc 01/`) are grouped per disc folder, so each disc is matched on its own, the runtime evidence is one disc long (the wrong edition can win), and `book` logs as `cd1..cdN`. Two releases that both use `cd1/` were also merged into one book. Fixed: grouping walks past disc parents to the release folder | #26 |
| 8 | Everything around a run lived in per-host wrapper scripts (ntfy summary, Audiobookshelf scan, inode de-dupe, an ASIN fixer that rewrote `fix.csv`). Added as config, off by default: `notify`, `abs`, `dedupe_roots`, `--pin RELEASE=ASIN` with `--remember` to keep the correction in the hints file (see "Correcting a match" in CONFIG.md) | — |
| 9 | A tagged ASIN Audible has no product for (skeleton answer) or that names another book ends the Audible search: the per-ASIN endpoint ignores title/author and no plain search followed. Fixed in 3.0.2: fall back to the title/author search through the same gates, as pinned ASINs already did (see CONFIG.md, release-name parsing) | — |
| 7 | mousehole cookie integration. Fixed: `mousehole_state_file` / `MOUSEHOLE_STATE_FILE`, reads mousehole's v2 (`cookie`) and legacy (`currentCookie`) state files (PR #24 read only the legacy key) | #24 |

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
