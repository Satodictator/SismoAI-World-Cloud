#!/usr/bin/env bash
set -euo pipefail
SOURCE_INPUT="${1:?Debe indicar la carpeta de estado a publicar}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN no disponible}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY no disponible}"

if [[ ! -d "$SOURCE_INPUT" ]]; then
  echo "No existe la carpeta de estado: $SOURCE_INPUT" >&2
  exit 1
fi

SOURCE="$(cd "$SOURCE_INPUT" && pwd -P)"
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

if git diff --cached --quiet; then
  echo "No hay estado para publicar." >&2
  exit 1
fi

git commit -m "state: ${SISMOAI_MODE:-unknown} ${GITHUB_RUN_ID:-local}" >/dev/null
git push --quiet --force origin HEAD:state
echo "Rama state publicada correctamente."
