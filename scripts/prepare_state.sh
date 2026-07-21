#!/usr/bin/env bash
set -euo pipefail
DEST="${1:-state_input}"
rm -rf "$DEST"
mkdir -p "$DEST/state/regions" "$DEST/results/regions" "$DEST/history/world"
printf 'state initialized\n' > "$DEST/.state_initialized"
if git fetch --quiet origin state:refs/remotes/origin/state 2>/dev/null; then
  git archive refs/remotes/origin/state | tar -x -C "$DEST"
  echo "Estado previo restaurado desde la rama state."
else
  echo "No existe rama state; se iniciará un estado limpio."
fi
