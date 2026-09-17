# Changelog

All notable changes to this fork. Upstream history before the fork point is in the upstream repository; the fork
point is tag `upstream-baseline`.

## Unreleased

### Added
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
- `Config/dedupe_roots`: a multi-file release whose discs are already hardlinked under *different* library
  folders (a prior `multibook` run, or a file moved in the library) is no longer reported as already filed.
  The first folder used to win, `hardlinkUnlessFiled` skipped, and the processed marker then hid the rest of
  the files on later runs.

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
