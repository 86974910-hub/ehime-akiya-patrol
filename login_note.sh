#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/Users/hktmacmini/Misato/Ehime_Akiya_Patrol"
PROFILE_DIR="${BASE_DIR}/.chrome_profile"
mkdir -p "${PROFILE_DIR}"

echo "専用ブラウザプロファイル (${PROFILE_DIR}) で Google Chrome を起動します..."
echo "note.com にログインし、記事作成エディタ画面が開くことを確認してから、ブラウザを完全に終了（Cmd+Q）してください。"

"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="${PROFILE_DIR}" \
  --no-first-run \
  --no-default-browser-check \
  "https://note.com/notes/new"
