# Changelog

All notable changes to this fork. Upstream history before the fork point is in the upstream repository; the fork
point is tag `upstream-baseline`. Versions continue upstream's numbering (its last tag was `2.2.0-beta`); the fork's
first release is 3.0.0 because exit codes and the cookie store changed in ways a caller can notice.

## Unreleased

## 3.0.3 - 2026-09-22

### Fixed
- A series, title or part rendered as `..` (or `.`) by a target_path template was left alone by the filename
  sanitiser and walked one level up: inside the media root with the default templates, outside it with a
  template of bare tokens such as `{series}/{part}/{title}`. Such a component now becomes `_` (in `disc_folder`
  too), and the finished target path is checked against the media root before any file is linked or copied. In
  `metadata: log` mode the log's `paths` column, written by an earlier run or edited by hand, is checked the same
  way against the run's `Config/paths` media roots.

### Fixed
- Two releases that both use a bitrate folder (`Title/64k/`, `Title/MP3/128kbps/`) were grouped as one
  book named `64k` and hardlinked to the first match. Grouping now walks past bitrate folders the same
  way it already walks past `MP3/` and `cd1/`. `cd1/MP3/64k/01.mp3` and `cd2/MP3/64k/01.mp3` also get
  distinct disc subfolders; they used to collide in a flat `Author/Title/` folder and the second disc
  was skipped.
- An Audible series entry without a `sequence` (or MAM `series_info` with only the series name) aborted the
  whole run in `product2Book` / `getMAMBook`. Unnumbered series entries now keep the series name and use
  `in_series_no_part`; they no longer file as `Series #None - Title`.

## 3.0.2 - 2026-09-22

### Fixed
- A file tagged with an ASIN that Audible has no product for (a withdrawn or duplicate listing answered with the
  skeleton `{asin, asset_details, is_vvab}`), or one that points at a different book, was left unmatched: the
  per-ASIN lookup ignores the title/author parameters and nothing else was tried. Such a file is now searched by
  its title and author like a tagless one, through the same title/author and duration gates
  (`Tagged ASIN X gave no usable Audible match; searching by title and author instead`). Pinned ASINs already
  fell back this way. Seen 2026-09-22 with *This Book Made Me Think of You* tagged `B0G2TK17DS` (dead on every
  marketplace); the fallback finds `B0FBHZK5V7`, duration difference 0 min. A live tag still resolves on the
  first call with no extra query. Applies to a `[bracketed]` ASIN in the release name as well. The JSON run log
  records such a match as `attempt: asin-fallback:<rung>`.
- Interactive mode auto-accepted a skeleton per-ASIN answer as the lone result and would have filed the book
  under an empty title; skeletons are now dropped before the choice is offered.

## 3.0.1 - 2026-09-19

### Fixed
- Two releases that both use a codec folder (`Title/MP3/`, `Title/M4B/`) were grouped as one book
  named `MP3` and hardlinked to the first match. Grouping now walks past codec folders the same way
  it already walks past `cd1/`. `cd1/MP3/01.mp3` and `cd2/MP3/01.mp3` also get distinct disc
  subfolders; they used to collide in a flat `Author/Title/` folder and the second disc was skipped.

## 3.0.0 - 2026-09-18

First release of the fork. Every item of the roadmap in docs/FORK.md is included; outputs stay compatible with
upstream (same run-log columns, stdout phrases and target layout). Image: `ghcr.io/mancolt/booktree:3.0.0`
(also `:3.0`, `:latest`).

Upgrading from `myxdvz/booktree:latest`: nothing in a config file has to change. Worth doing: replace `"\bpart\b"`
style `title_patterns` with `"\\bpart\\b"` (a repaired pattern is reported at start-up), move the MAM cookie out
of the config into `MAM_SESSION_FILE`, and read the exit code (0 ok, 1 error, 2 configuration, 130 interrupted)
instead of grepping stdout. `cookies.pkl` is removed on first start and replaced by `cookies.json`.

### Added
- `Config/target_path/in_series_no_part` (default `{author}/{series}/{series} - {title}`): the template for a
  book in a series whose part is unknown. `in_series` rendered `{part}` empty and produced folders such as
  `Jack Reacher # - Three More Jack Reacher Novellas` (upstream #27); two unnumbered entries of one series could
  collide. Set it to your `in_series` value to keep the old names. Books already filed are not moved.
- `--pin RELEASE=ASIN` (repeatable; `Config/pins`): use that Audible product for the release and re-process it
  now, ignoring its cached answers and processed marker. Shorthand for a hint `{"asin": ..., "refresh": true}`;
  a malformed pin exits 2, an unused one is reported at the end of the run.
- `--remember` (`Config/remember_pins`): after the run, pins whose Audible product was accepted are written into the hints
  file as `{"asin": ...}` so the correction persists; other entries are kept, the write is atomic, an unparsable
  file is never overwritten. CONFIG.md and the README gained a "Correcting a match" section.
- `Config/notify`: end-of-run summary to an [ntfy](https://ntfy.sh) topic (`ntfy_url`, token via `NTFY_TOKEN`;
  `on` = always / unmatched / failure) and a `heartbeat_url` fetched after a clean run. Best effort; URLs are not
  printed.
- `Config/abs`: request an Audiobookshelf library scan (`url`, `library_id`; token via `ABS_API_TOKEN` or
  `ABS_API_TOKEN_FILE`) after a run that created hardlinks.
- `Config/dedupe_roots`: a matched release whose files are already hardlinked under one of the listed
  directories is reported (`already_filed` in the JSON log) and not hardlinked again; nothing is deleted. A root
  containing a `source_path` is refused.
- MAM session from the environment: when `Config/session` is empty, `MAM_SESSION` or the first line of the file
  named by `MAM_SESSION_FILE` (a docker/compose secret) is used, so the cookie no longer has to be repeated in
  every config file.
- mousehole integration (upstream PR #24, reworked): `Config/mousehole_state_file` or `MOUSEHOLE_STATE_FILE`
  names a [mousehole](https://github.com/t-mart/mousehole) `state.json`; the cookie is read from it on every run
  and sent verbatim. Reads mousehole's current schema (`version: 2`, key `cookie`) as well as the legacy
  `currentCookie` the PR targeted. Falls back to the cookie store and the config session when the file is
  unusable. Nothing is written back.
- Exit codes: 0 when every configured path was processed, 2 for configuration/input problems (config file,
  paths, hints file, log-mode input, MAM session), 1 for an unhandled error, 130 when interrupted. Configuration
  problems print a message instead of a traceback. With `--json-log`, a run that fails with an unhandled error
  still appends a `run` record carrying `exit_code` and `error`.

### Fixed
- Series parts survive a trip through the run log. The log writes `seriesparts` as `Name part` while the
  log-mode reader (`fix.csv`) split on `#`, so the whole string became the series name and the part was lost
  (upstream #27, half of it). The reader now pairs `seriesparts` with the `series` column; a decimal part is
  logged as `17.5` instead of `17 5` (the only visible change in the corpus replay: the fuzzy-match string of four
  novellas, same matches).
- `Config/dedupe_roots`: a multi-file release whose discs are already hardlinked under *different* library
  folders (a prior `multibook` run, or a file moved in the library) is no longer reported as already filed.
  The first folder used to win, `hardlinkUnlessFiled` skipped, and the processed marker then hid the rest of
  the files on later runs. The `Title/cd1`, `Title/cd2` disc subfolders booktree creates still count as one book.

### Changed
- The cookie store is `<log_path>/cookies.json` (owner-readable, written atomically) instead of `cookies.pkl`.
  A pickle from a shared directory was loaded on every run, which executes whatever the file contains; an existing
  `cookies.pkl` is removed, never loaded, and the next run re-validates from the config/env session. The MAM
  cookie is checked once per run at start-up; the session check before the first search is skipped when that
  check already passed (one MAM request fewer per run). A session rejected by MAM falls through to the next
  source (mousehole → cookie store → config/env) instead of failing outright; when MAM cannot be reached at all
  the store is kept (upstream deleted the pickle on any error). The cookie is kept in the session's jar under
  MAM's own domain, so a rotation MAM sends back replaces it and is the value persisted. Session files are read
  through the descriptor (a planted FIFO or a huge file at the path cannot hang the run) and a value that is not
  a valid cookie value is refused before any request is made.
- CI: bandit now gates on medium severity (`-ll`) and the `S301` (pickle) lint exemption is gone.
- Cache freshness (`cache/*_hours`): empty Audible answers (and title-less per-ASIN skeletons) are retried after
  6 hours, non-empty ones after 30 days; MAM answers after 24 hours / 7 days. Errors are never cached. Same
  cache files and names as upstream. Retires the weekly prune script.
- MAM traffic rules: requests spaced 6 s apart (the safety net); a per-run cap of 3000 searches as a runaway
  guard (`mam/*`). Empty MAM answers are now
  cached for 24 h instead of being re-asked on every run.
- `--refresh RELEASE` (or `"refresh": true` in a hint): re-process one release ignoring its cached answers and
  processed marker.
- `--json-log [PATH]`: JSON-lines run log with one record per book (parsed name, hint, pin, match, attempt,
  runtime delta, target path, every query with its cache key) and a run summary. Additive: CSV and stdout unchanged.
  Writing it can never fail the run; log files are opened without following symlinks (the CSV too).
- Hints are looked up before the "already processed" check, so `Applying hint for …` now precedes `Processing: …`
  and also appears for releases that are skipped as already processed.
- The MAM session cookie is tested once per run instead of before every search (halves MAM requests).
- Release-name parsing before the search (`flags/parse_names`, default on; `--legacy-names` restores the old
  behaviour): `Author - Title`, `Title - Author`, `Title by Author`, `Series NN - Title`, `Title [ASIN]`,
  `(Unabridged)` and format noise. Used only where the id3 tags are empty or junk; title and author are sent to
  Audible as separate fields, with a swapped-reading retry for ambiguous names and a title-only retry. See CONFIG.md.
- Image tags: every merge to `main` publishes `:edge`; `:latest` and version tags are published from `v*` tags only.
- `--hints <json>` (or `Config/hints_file`): per-release pinned ASIN, candidate ASINs, expected duration and
  search title/authors, keyed by release folder, file name or path. See CONFIG.md.
- Duration as evidence: among acceptable Audible results, one whose runtime is within 2 minutes of the files'
  total duration (or the hint's `duration_min`) is preferred over a higher fuzzy score with the wrong runtime;
  equal scores prefer the closer runtime.
- `Config/pin_max_runtime_delta_min`: optional hard limit on the runtime mismatch of a pinned ASIN (default off).

### Fixed
- `disk N/` and `part N/` folders, which are grouped into one book since the multi-disc fix below, were filed
  into a flat `Author/Title/` folder, so `Disk 1/01.mp3` and `Disk 2/01.mp3` collided and the second was
  silently skipped. They now get a `disc_folder` subfolder like `cd N/` and `disc N/` always did.
- Multi-disc releases (`cd1/`, `Disc 01/`, `part 2/`) were grouped by the disc folder. Two downloads that both
  used `cd1/` became one book (files from both hardlinked to the first match) and a single release's discs were
  matched separately with one-disc runtime (the wrong edition could win). Grouping now walks past disc parents
  to the release folder; `Author/Title` layouts and loose files are unchanged. `--pin`/`--refresh` by release
  name also sees files inside `cd1/`. A later disc under a `last_scan` cutoff is scanned with its siblings
  and re-processed even if the release was cached when only the first disc existed.
- `title_patterns`: the template and CONFIG.md wrote word boundaries as `"\bpart\b"`, which JSON reads as the word
  between two backspace characters, so `part`, `track`, `of` and `book` were never removed from an alternative
  title. The examples now read `"\\bpart\\b"`, and a pattern containing a backspace is read as the intended `\b`
  (with a one-time note), so existing config files work without editing.
- A usable id3 title is no longer overridden by an ASIN parsed from the release name. `applyParsedName`
  used to send that ASIN to Audible even when the tags were good, and `_rankAudible` skipped the title
  gate (`requireTitle` is only set when the parsed title replaced a junk tag), so a leftover or wrong
  ASIN in the folder name (`Brad Thor - Takedown [B0WRONG001]`) filed the book under a different title
  by the same author.
- A usable id3 title is no longer paired with a parsed folder author under the author-only
  gate. `requireTitle` was set only when the parsed title replaced a junk tag, so junk
  artist + `Author - Title` in the folder name accepted that author's other books
  (`James Patterson - The Guest` with id3 title `The Guest` filed Along Came a Spider).
  The same title gate is applied to MAM ranking, comparing against the id3/parsed
  title rather than the file basename.
- In `log` mode an explicit `id3-asin` (fix.csv) is authoritative: the Audible product for that ASIN is accepted
  even when the id3 title/author disagree, instead of being vetoed by the title/author comparison.
- Audible answers some per-ASIN lookups with a skeleton `{asin, asset_details, is_vvab}` and no title. These are
  now skipped instead of being compared against empty strings and rejected (upstream issue #25).
- A release whose files report no duration no longer raises in the runtime calculation.
- Author and series names are escaped before being used as regular expressions when deriving an alternative
  title; a name such as `Brad [` (from a tag or a hint) used to abort the run, and a crafted one could hang it.
- A malformed `AUDIBLE_ASIN` tag is ignored (with a message) instead of being sent to Audible's per-ASIN URL,
  which guaranteed a miss.
- A file name that is not valid UTF-8 no longer aborts the run when its cache key is computed.
- `metadata.opf` is now well-formed XML for any metadata: `&`, `<`, `>` and quotes are escaped in every text node
  and attribute, CDATA sections survive a literal `]]>`, characters XML forbids are stripped, and a backslash in a
  description no longer aborts OPF generation. Audiobookshelf used to discard the whole file, ASIN included, on
  the first bare `&` (e.g. publisher `Little, Brown & Company`). Output for metadata without special characters
  is byte-identical to before. Template tokens are substituted in a single pass, so metadata that itself contains
  a token (an author named `__DESCRIPTION__`) can no longer pull another field, unescaped, into its place.
- The OPF template is resolved relative to the module, not the current working directory.

### Changed
- Image published as `ghcr.io/mancolt/booktree` (amd64, arm64) with SBOM and provenance; base pinned to Alpine 3.21;
  no package installer left in the runtime image.
- CI: lint, unit tests, bandit, pip-audit, gitleaks, Trivy image scan; Dependabot for pip, actions and docker.
