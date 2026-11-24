#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/Users/hktmacmini/Misato/Ehime_Akiya_Patrol"
PYTHON="${BASE_DIR}/.venv/bin/python"

echo "Playwright管理下の専用プロファイル (${BASE_DIR}/.chrome_profile) で Google Chrome を起動します..."
echo "note.com にログインし、記事作成エディタ画面が開くことを確認してから、ブラウザのウィンドウを閉じてください。"

"${PYTHON}" -c '
from pathlib import Path
from playwright.sync_api import sync_playwright

profile = Path("/Users/hktmacmini/Misato/Ehime_Akiya_Patrol/.chrome_profile")
profile.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=False,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        viewport={"width": 1280, "height": 960},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://note.com/notes/new")
    print("ブラウザが閉じるのを待機しています...")
    try:
        page.wait_for_event("close", timeout=0)
    except Exception:
        pass
    ctx.close()
'
