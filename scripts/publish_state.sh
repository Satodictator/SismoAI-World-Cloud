#!/usr/bin/env bash
set -euo pipefail
SOURCE="${1:?Debe indicar la carpeta de estado a publicar}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN no disponible}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY no disponible}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
git clone --quiet --no-checkout "$URL" "$TMP/repo"
cd "$TMP/repo"
git checkout --orphan state-build >/dev/null 2>&1
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$SOURCE"/. .
git config user.name "sismoai-world-bot"
git config user.email "sismoai-world-bot@users.noreply.github.com"
git add -A
git commit -m "state: ${SISMOAI_MODE:-unknown} ${GITHUB_RUN_ID:-local}" >/dev/null
git push --quiet --force origin HEAD:state
echo "Rama state publicada correctamente."
