#!/usr/bin/env bash
set -euo pipefail
DEST="${1:-state_input}"
rm -rf "$DEST"
mkdir -p "$DEST/state/regions" "$DEST/results/regions" "$DEST/history/world"
printf 'state initialized\n' > "$DEST/.state_initialized"
printf 'placeholder\n' > "$DEST/state/regions/EMPTY_STATE"
printf 'placeholder\n' > "$DEST/results/regions/EMPTY_RESULTS"
printf 'placeholder\n' > "$DEST/history/world/EMPTY_HISTORY"
if git fetch --quiet origin state:refs/remotes/origin/state 2>/dev/null; then
  git archive refs/remotes/origin/state | tar -x -C "$DEST"
  echo "Estado previo restaurado desde la rama state."
else
  echo "No existe rama state; se iniciará un estado limpio."
fi
