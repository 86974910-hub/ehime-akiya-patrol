#!/usr/bin/env python3
"""
愛媛の空き家パトロール：note.com 自動下書き保存スクリプト（公開ボタンは絶対に押さない）
※ SSママ等既存環境とは完全に独立した専用ブラウザプロファイル（.chrome_profile）のみを使用する。
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Tuple
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / ".chrome_profile"
DRAFTS_DIR = BASE_DIR / "drafts"
LOGS_DIR = BASE_DIR / "logs"

URL_TOKEN_RE = re.compile(r"(https?://[^\s\)\]>]+)")
TABLE_SEP_RE = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def extract_title_and_body_from_md(md_text: str) -> Tuple[str, str]:
    lines = md_text.strip().splitlines()
    if not lines:
        raise ValueError("Markdownファイルが空です。")
    first = re.sub(r"^#+\s*", "", lines[0]).strip()
    body_lines = lines[1:] if len(lines) > 1 else []
    return first, "\n".join(body_lines).strip()


def format_markdown_for_note(text: str) -> str:
    """
    Markdownをnoteエディタ（ProseMirror）へ安全に投入できる形式へ整形する。
    - 太字 ** を除去（生アスタリスク残存防止）
    - Markdownテーブルをnoteで読みやすい箇条書きへ変換
    - 見出し（## / ###）を視認性の高い記号付き見出しへ変換
    """
    out = []
    in_table = False
    for raw_line in text.replace("**", "").splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()

        if TABLE_SEP_RE.match(stripped):
            in_table = True
            continue

        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                in_table = True
                out.append("【" + " ｜ ".join(c for c in cells if c) + "】")
                continue
            out.append("・" + " ｜ ".join(c for c in cells if c))
            continue
        else:
            in_table = False

        if stripped.startswith("### "):
            out.append(f"◆ {stripped[4:].strip()}")
        elif stripped.startswith("## "):
            out.append(f"■ {stripped[3:].strip()}")
        elif stripped == "---":
            out.append("━━━━━━━━━━━━━━━━━━━━")
        else:
            out.append(line)

    return "\n".join(out)


def dismiss_interfering_dialogs(page):
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    selectors = [
        "[role='dialog'] button:has-text('破棄する')",
        "[role='dialog'] button:has-text('閉じる')",
        "[role='dialog'] button:has-text('キャンセル')",
        "[aria-modal='true'] button:has-text('破棄する')",
        "[aria-modal='true'] button:has-text('閉じる')",
        "[data-testid='modal-close-button']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=400):
                btn.click()
                log(f"妨害ダイアログを解除しました: {sel}")
                page.wait_for_timeout(400)
                break
        except Exception:
            pass


def find_draft_save_button(page):
    """下書き保存ボタンを探索（公開に進むボタンとは完全に区別）"""
    selectors = [
        "button:has-text('下書き保存')",
        "button:has-text('下書きを保存')",
        "//button[contains(., '下書き')]",
        "button[data-testid='draft-save-button']",
        "button[aria-label*='下書き保存']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                return btn, sel
        except Exception:
            continue
    try:
        for b in page.locator("button").all():
            txt = (b.inner_text() or "").strip()
            if "下書き" in txt and "公開" not in txt:
                return b, f"button-text-scan('{txt}')"
    except Exception:
        pass
    return None, None


def paste_body_lines(page, body: str):
    prepared = format_markdown_for_note(body)
    lines = prepared.split("\n")
    for i, line in enumerate(lines):
        line_clean = line.strip("\r")
        if line_clean:
            stripped = line_clean.strip()
            if re.fullmatch(r"https?://\S+", stripped):
                page.keyboard.insert_text(stripped)
                page.wait_for_timeout(200)
                page.keyboard.press("Enter")
                page.wait_for_timeout(450)
                continue
            elif URL_TOKEN_RE.search(line_clean):
                parts = URL_TOKEN_RE.split(line_clean)
                for idx, seg in enumerate(parts):
                    if not seg:
                        continue
                    if URL_TOKEN_RE.fullmatch(seg):
                        page.keyboard.insert_text(seg)
                        page.wait_for_timeout(50)
                        next_seg = parts[idx + 1] if idx + 1 < len(parts) else ""
                        if not next_seg.startswith(" "):
                            page.keyboard.press("Space")
                            page.wait_for_timeout(50)
                    else:
                        page.keyboard.insert_text(seg)
                        page.wait_for_timeout(15)
            else:
                page.keyboard.insert_text(line_clean)
                page.wait_for_timeout(12)
        if i < len(lines) - 1:
            page.keyboard.press("Enter")
            page.wait_for_timeout(20)


def save_note_draft(
    title: str,
    body: str,
    headless: bool = True,
    screenshot_name: str = "note_draft_latest.png",
) -> str:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    log(f"note下書き保存開始: 『{title}』 (元文字数={len(body)}, profile={PROFILE_DIR})")
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            channel="chrome",
            ignore_default_args=["--use-mock-keychain", "--password-store=basic"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        )
        try:
            page = browser.new_page()
            page.add_init_script(
                """
                Object.defineProperty(navigator, "webdriver", { get: () => undefined });
                window.navigator.chrome = { runtime: {} };
                """
            )
            page.goto("https://note.com/notes/new", wait_until="networkidle", timeout=45000)
            try:
                page.wait_for_selector("textarea, .ProseMirror, [contenteditable='true']", timeout=30000)
            except Exception:
                log("WARN: エディタ要素の初期待機タイムアウト、画面確認を続行します")
            time.sleep(2)

            if "login" in page.url:
                page.screenshot(path=str(LOGS_DIR / "note_login_required.png"), full_page=False)
                raise RuntimeError(
                    f"note未ログイン状態です (URL={page.url})。先に {BASE_DIR / 'login_note.sh'} を実行して専用プロファイルにログインしてください。"
                )

            dismiss_interfering_dialogs(page)

            # タイトル入力
            title_filled = False
            for sel in (
                "textarea[placeholder*='記事タイトル']",
                "textarea[placeholder*='タイトル']",
                "textarea.note-editor-title",
                "textarea",
            ):
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=2000):
                        loc.fill(title)
                        title_filled = True
                        log(f"タイトル入力完了 ({sel})")
                        break
                except Exception:
                    continue
            if not title_filled:
                raise RuntimeError("noteタイトル欄が見つかりません。")

            time.sleep(0.5)

            # 本文入力
            body_el = None
            for sel in (".ProseMirror", "div[contenteditable='true']", "[role='textbox']"):
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=2000):
                        body_el = loc
                        body_el.click()
                        break
                except Exception:
                    continue
            if not body_el:
                raise RuntimeError("note本文エディタ (.ProseMirror) が見つかりません。")

            paste_body_lines(page, body)
            page.wait_for_timeout(1500)

            rendered_len = len((body_el.inner_text() or "").strip())
            log(f"エディタ反映文字数: {rendered_len}")
            if rendered_len < 50:
                raise RuntimeError(f"本文がエディタに反映されませんでした (文字数={rendered_len})")

            dismiss_interfering_dialogs(page)

            # 下書き保存ボタンのみクリック（公開ボタンは絶対に押さない）
            btn, sel = find_draft_save_button(page)
            if not btn:
                page.screenshot(path=str(LOGS_DIR / "note_draft_error.png"), full_page=True)
                raise RuntimeError("下書き保存ボタンが見つかりませんでした。")

            for _ in range(10):
                try:
                    if not btn.is_disabled():
                        break
                except Exception:
                    pass
                page.wait_for_timeout(400)

            btn.click()
            log(f"「下書き保存」をクリックしました ({sel})")
            time.sleep(4)
            draft_url = page.url
            shot_path = LOGS_DIR / screenshot_name
            page.screenshot(path=str(shot_path), full_page=False)
            log(f"note下書き保存完了: {draft_url} (screenshot: {shot_path})")
            return draft_url
        finally:
            browser.close()


def main():
    ap = argparse.ArgumentParser(description="note下書き自動保存（公開はしない）")
    ap.add_argument("--draft-file", "--draft-json", dest="draft_file", type=str, default=None, help="daily_*_note.json または .md のパス")
    ap.add_argument("--no-headless", dest="headless", action="store_false", default=True, help="ブラウザを表示して実行")
    ap.add_argument("--screenshot-name", type=str, default="note_draft_latest.png", help="logs/配下に保存するスクリーンショット名")
    args = ap.parse_args()

    if args.draft_file:
        fp = Path(args.draft_file)
    else:
        candidates = sorted(DRAFTS_DIR.glob("daily_*_note.json"))
        if not candidates:
            log("保存対象の *_note.json が見つかりません。終了します。")
            return
        fp = candidates[-1]

    if not fp.exists():
        raise FileNotFoundError(f"指定ファイルが存在しません: {fp}")

    if fp.suffix.lower() == ".json":
        data = json.loads(fp.read_text(encoding="utf-8"))
        title, body = data["title"], data["body"]
    else:
        title, body = extract_title_and_body_from_md(fp.read_text(encoding="utf-8"))

    save_note_draft(title, body, headless=args.headless, screenshot_name=args.screenshot_name)


if __name__ == "__main__":
    main()
