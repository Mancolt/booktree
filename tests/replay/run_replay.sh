#!/bin/bash
# Replay the historical corpus offline inside a booktree image.
# usage: tests/replay/run_replay.sh <image> <out-dir> [--limit N]
# Mounts: repo -> /booktree (ro, overrides the image's code), logs -> /logs (ro), a *copy* of the
# cache + session-less configs -> /Config (ro).  Nothing on the live host is written.
set -euo pipefail
IMAGE=${1:?image}; OUT=${2:?out dir}; shift 2
REPO=$(cd "$(dirname "$0")/../.." && pwd)
LOGS=${BOOKTREE_LOGS:?set BOOKTREE_LOGS to the directory holding booktree_log_*.csv}
CONFIG=${BOOKTREE_CONFIG:?set BOOKTREE_CONFIG to the directory holding __cache__/ and the *.json configs}
STAGE=${BOOKTREE_STAGE:-$(mktemp -d)}
mkdir -p "$OUT" "$STAGE/Config"
# snapshot cache + configs (session blanked) so the container never sees the live dirs read-write
rsync -a --delete "$CONFIG/__cache__" "$STAGE/Config/"
for c in "$CONFIG"/*.json; do
  sed -E 's/("session"[[:space:]]*:[[:space:]]*")[^"]*"/\1"/' "$c" > "$STAGE/Config/$(basename "$c")"
done
docker run --rm --user root -e PYTHONDONTWRITEBYTECODE=1 -w /booktree \
  -v "$REPO:/booktree:ro" -v "$LOGS:/logs:ro" -v "$STAGE/Config:/Config:ro" -v "$OUT:/out" \
  "$IMAGE" /venv/bin/python3 /booktree/tests/replay/replay_corpus.py \
  --config pass1=/Config/config.json --config pass2=/Config/config-audible.json --out /out "$@"
