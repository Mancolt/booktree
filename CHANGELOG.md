# Changelog

All notable changes to this fork. Upstream history before the fork point is in the upstream repository; the fork
point is tag `upstream-baseline`.

## Unreleased

### Fixed
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
