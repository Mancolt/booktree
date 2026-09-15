#!/bin/bash
# Replay the historical corpus offline inside a booktree image.
# usage: tests/replay/run_replay.sh <image> <out-dir> [--limit N]
# Mounts: repo -> /booktree (ro, overrides the image's code), logs -> /logs (ro), a *copy* of the
# cache + session-less configs -> /Config (ro).  Nothing on the live host is written.
set -euo pipefail
IMAGE=${1:?image}; OUT=${2:?out dir}; shift 2
REPO=$(cd "$(dirname "$0")/../.." && pwd)
# BOOKTREE_CODE: the booktree source tree to replay (default: this repo). Point it at a pristine upstream checkout
# to reproduce upstream behaviour with the same harness; the image only supplies the Python runtime.
CODE=${BOOKTREE_CODE:-$REPO}
LOGS=${BOOKTREE_LOGS:?set BOOKTREE_LOGS to the directory holding booktree_log_*.csv}
CONFIG=${BOOKTREE_CONFIG:?set BOOKTREE_CONFIG to the directory holding __cache__/ and the *.json configs}
STAGE=${BOOKTREE_STAGE:-$(mktemp -d)}
case "$(realpath -m "$STAGE")/" in "$(realpath -m "$CONFIG")/"*) echo "refusing: BOOKTREE_STAGE is inside BOOKTREE_CONFIG (would alias the live cache)" >&2; exit 1;; esac
mkdir -p "$OUT" "$STAGE/Config"
# snapshot cache + configs (session blanked) so the container never sees the live dirs read-write
rsync -a --delete "$CONFIG/__cache__" "$STAGE/Config/"
for c in "$CONFIG"/*.json; do
  sed -E 's/("session"[[:space:]]*:[[:space:]]*")[^"]*"/\1"/' "$c" > "$STAGE/Config/$(basename "$c")"
done
# BOOKTREE_REPLAY_LIVE=1: uncached Audible queries are fetched from the real API and cached into the staged copy
CFG_MODE=ro; LIVE=()
if [ "${BOOKTREE_REPLAY_LIVE:-0}" = "1" ]; then CFG_MODE=rw; LIVE=(--audible-live); fi
docker run --rm --user root -e PYTHONDONTWRITEBYTECODE=1 -w /booktree \
  -v "$CODE:/booktree:ro" -v "$REPO/tests/replay:/replay:ro" -v "$LOGS:/logs:ro" -v "$STAGE/Config:/Config:$CFG_MODE" -v "$OUT:/out" \
  "$IMAGE" /venv/bin/python3 /replay/replay_corpus.py \
  --config pass1=/Config/config.json --config pass2=/Config/config-audible.json --out /out "${LIVE[@]}" "$@"
