# Changelog

All notable changes to this fork. Upstream history before the fork point is in the upstream repository; the fork
point is tag `upstream-baseline`.

## Unreleased

### Added
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
- A usable id3 title is no longer overridden by an ASIN parsed from the release name. `applyParsedName`
  used to send that ASIN to Audible even when the tags were good, and `_rankAudible` skipped the title
  gate (`requireTitle` is only set when the parsed title replaced a junk tag), so a leftover or wrong
  ASIN in the folder name (`Brad Thor - Takedown [B0WRONG001]`) filed the book under a different title
  by the same author.
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
