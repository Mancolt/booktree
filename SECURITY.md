# Security policy

booktree runs unattended against a download folder, holds a private-tracker session cookie, and writes into a media
library. Reports about any of the following are in scope: writes outside the configured media path, anything that
touches files under the source path, credential leakage (config, logs, cache, stdout), unsafe deserialization,
injection through file names or metadata (paths, OPF/XML, shell), and dependency vulnerabilities.

Please report privately through GitHub's "Report a vulnerability" (Security tab) rather than a public issue. You will
get an acknowledgement within a week. Fixes ship as a new image tag; the advisory is published once a fixed image exists.

Hardening in this fork: the container runs as a non-root user; secrets can be supplied by environment variable or
secret file instead of the config file; CI runs gitleaks, bandit, pip-audit and a Trivy scan of the built image.
