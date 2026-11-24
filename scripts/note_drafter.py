#!/usr/bin/env python3
"""
愛媛の空き家パトロール：note.com 自動下書き保存スクリプト（公開ボタンは絶対に押さない）
※ SSママ等既存環境とは完全に独立した専用ブラウザプロファイル（.chrome_profile）のみを使用する。

【リッチテキスト＆クリーン整形仕様（SSママ知見＋noteネイティブ対応）】
- パイプ記号（| / ｜）による擬似テーブルやMarkdown表の残骸を完全排除（安全ガード付き）
- Markdownの見出し（## / ###）、太字（**...**）、箇条書き（- ）、引用（> ）、区切り線（---）、リンクを
  noteのProseMirrorエディタがネイティブHTML（<h2>, <h3>, <strong>, <ul><li>, <blockquote>, <hr>, <a>）として
  確実に解釈・装飾する方式で流し込む。
"""

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / ".chrome_profile"
DRAFTS_DIR = BASE_DIR / "drafts"
LOGS_DIR = BASE_DIR / "logs"

INLINE_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
URL_TOKEN_RE = re.compile(r"(https?://[^\s\)\]>]+)")
BOLD_TOKEN_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
TABLE_SEP_RE = re.compile(r"^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*$")


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def extract_title_and_body_from_md(md_text: str) -> Tuple[str, str]:
    lines = md_text.strip().splitlines()
    if not lines:
        raise ValueError("Markdownファイルが空です。")
    first = re.sub(r"^#+\s*", "", lines[0]).strip().replace("**", "")
    body_lines = lines[1:] if len(lines) > 1 else []
    body = "\n".join(body_lines).strip()
    # daily_YYYYMMDD.md のように「## 1. X・Threads投稿案」「## 2. note記事用本文」に分かれている場合はnote本文のみ抽出
    m_note_sec = re.search(r"##\s*2\.\s*note記事用本文[^\n]*\n+([\s\S]*)", body)
    if m_note_sec:
        body = m_note_sec.group(1).strip()
    return first, body


def sanitize_no_pipe_tables(text: str) -> str:
    """
    万が一原稿にパイプ記号（| や ｜）の表や擬似テーブルが含まれていても、
    パイプ記号を一切残さず自然な箇条書き・読点区切りへクレンジングする安全ガード。
    """
    if not text:
        return ""
    out_lines: List[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if TABLE_SEP_RE.match(s):
            continue
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip("|").split("|") if c.strip() and c.strip() != "-"]
            if cells:
                out_lines.append("- " + " ／ ".join(cells))
            continue
        # 行内のパイプ記号（| / ｜）も自然な区切り（／ または ・）へ変換
        cleaned = raw.replace("|", "／").replace("｜", "・")
        out_lines.append(cleaned)
    return "\n".join(out_lines)


def render_inline_html(text: str) -> str:
    """
    行内の太字 `**...**`、Markdownリンク `[ラベル](URL)`、裸のURL `https://...` を
    リッチテキストHTML（<strong>, <a href="...">）に変換する。
    """
    # まずプレースホルダに退避しながらHTMLエスケープ
    tokens: List[str] = []

    def _stash(html_fragment: str) -> str:
        idx = len(tokens)
        tokens.append(html_fragment)
        return f"\x00TOK{idx}\x00"

    # 1. Markdownリンク [label](url) -> <a>
    def _repl_md_link(m: re.Match) -> str:
        label_raw = m.group(1).strip()
        url_raw = m.group(2).strip()
        # ラベル内の **太字** も反映
        label_escaped = html.escape(label_raw)
        label_html = BOLD_TOKEN_RE.sub(r"<strong>\1</strong>", label_escaped)
        url_escaped = html.escape(url_raw, quote=True)
        return _stash(f'<a href="{url_escaped}" target="_blank" rel="noopener noreferrer">{label_html}</a>')

    work = INLINE_MD_LINK_RE.sub(_repl_md_link, text)

    # 2. 裸のURL -> <a>
    def _repl_bare_url(m: re.Match) -> str:
        u = m.group(1).strip()
        u_esc = html.escape(u, quote=True)
        return _stash(f'<a href="{u_esc}" target="_blank" rel="noopener noreferrer">{u_esc}</a>')

    work = URL_TOKEN_RE.sub(_repl_bare_url, work)

    # 3. 残りのテキストをエスケープし、**太字** を <strong> に変換
    work = html.escape(work)
    work = BOLD_TOKEN_RE.sub(r"<strong>\1</strong>", work)
    # 孤立した ** があれば除去（生アスタリスク残存防止）
    work = work.replace("**", "")

    # 4. 退避したHTMLトークンを復元
    for idx, frag in enumerate(tokens):
        work = work.replace(f"\x00TOK{idx}\x00", frag)

    return work


def markdown_to_note_html_and_plain(md_text: str) -> Tuple[str, str]:
    """
    Markdownテキストを note の ProseMirror エディタが 100% ネイティブ装飾として受け取る
    セマンティックHTML（<h2>, <h3>, <p>, <strong>, <ul><li>, <blockquote>, <hr>, <a>）と
    プレーンテキストのペアに変換する。
    """
    cleaned = sanitize_no_pipe_tables(md_text)
    lines = cleaned.splitlines()

    html_blocks: List[str] = []
    plain_blocks: List[str] = []

    in_ul = False
    ul_items: List[str] = []
    in_quote = False
    quote_lines: List[str] = []

    def flush_ul():
        nonlocal in_ul, ul_items
        if in_ul and ul_items:
            li_html = "".join(f"<li><p>{item}</p></li>" for item in ul_items)
            html_blocks.append(f"<ul>{li_html}</ul>")
        in_ul = False
        ul_items = []

    def flush_quote():
        nonlocal in_quote, quote_lines
        if in_quote and quote_lines:
            q_inner = "<br>".join(quote_lines)
            html_blocks.append(f"<blockquote><p>{q_inner}</p></blockquote>")
        in_quote = False
        quote_lines = []

    for raw in lines:
        line = raw.rstrip("\r")
        stripped = line.strip()

        # 空行
        if not stripped:
            flush_ul()
            flush_quote()
            continue

        # 区切り線 (---)
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            flush_ul()
            flush_quote()
            html_blocks.append("<hr>")
            plain_blocks.append("──────────")
            continue

        # 引用ブロック (> ...)
        if stripped.startswith(">"):
            flush_ul()
            q_content = re.sub(r"^>+\s*", "", stripped)
            in_quote = True
            quote_lines.append(render_inline_html(q_content))
            plain_blocks.append(q_content.replace("**", ""))
            continue
        else:
            flush_quote()

        # 箇条書き (- ... / * ... / ・...)
        m_bullet = re.match(r"^(?:[-*]\s+|・\s*)(.+)$", stripped)
        if m_bullet:
            item_text = m_bullet.group(1).strip()
            in_ul = True
            ul_items.append(render_inline_html(item_text))
            plain_blocks.append("・" + item_text.replace("**", ""))
            continue
        else:
            flush_ul()

        # 小見出し (### ...) -> <h3>
        if stripped.startswith("### "):
            h3_text = stripped[4:].strip().replace("**", "")
            html_blocks.append(f"<h3>{render_inline_html(h3_text)}</h3>")
            plain_blocks.append(h3_text)
            continue

        # 大見出し (## ... / # ...) -> <h2>
        if stripped.startswith("## ") or stripped.startswith("# "):
            h2_text = re.sub(r"^#{1,2}\s+", "", stripped).strip().replace("**", "")
            html_blocks.append(f"<h2>{render_inline_html(h2_text)}</h2>")
            plain_blocks.append(h2_text)
            continue

        # 番号付きリスト (1. ... -> 1） ... でnote自動番号バグを防止しつつ段落出力)
        m_num = re.match(r"^(\d+)[\.\)]\s+(.+)$", stripped)
        if m_num:
            num_line = f"**{m_num.group(1)}）** {m_num.group(2)}"
            html_blocks.append(f"<p>{render_inline_html(num_line)}</p>")
            plain_blocks.append(num_line.replace("**", ""))
            continue

        # 通常の段落
        html_blocks.append(f"<p>{render_inline_html(stripped)}</p>")
        plain_blocks.append(stripped.replace("**", ""))

    flush_ul()
    flush_quote()

    return "\n".join(html_blocks), "\n\n".join(plain_blocks)


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


def paste_rich_markdown_into_note(page, body_md: str):
    """
    MarkdownをnoteネイティブHTML（<h2>, <h3>, <strong>, <ul><li>, <blockquote>, <hr>, <a>）へ変換し、
    ProseMirrorへ ClipboardEvent('paste') として一括注入する。
    これにより、見出し・太字・箇条書き・引用・リンクがプレーンテキストの記号のまま残らず、
    noteの標準リッチテキスト装飾として美しく反映される。
    """
    html_payload, plain_payload = markdown_to_note_html_and_plain(body_md)

    page.evaluate(
        """({ html, plain }) => {
            const pm = document.querySelector('.ProseMirror') || document.querySelector("[contenteditable='true']");
            if (!pm) throw new Error('ProseMirror editor not found');
            pm.focus();
            const dt = new DataTransfer();
            dt.setData('text/html', html);
            dt.setData('text/plain', plain);
            const evt = new ClipboardEvent('paste', {
                clipboardData: dt,
                bubbles: true,
                cancelable: true
            });
            pm.dispatchEvent(evt);
        }""",
        {"html": html_payload, "plain": plain_payload},
    )
    page.wait_for_timeout(1200)


def verify_rich_dom_elements(page) -> dict:
    """ProseMirror DOM 内に h2/h3/strong/ul/blockquote/a が正しく生成され、生の ## や ** や | が残っていないか検証する"""
    return page.evaluate(
        """() => {
            const pm = document.querySelector('.ProseMirror');
            if (!pm) return { len: 0 };
            const txt = pm.innerText || '';
            return {
                len: txt.trim().length,
                h2: pm.querySelectorAll('h2').length,
                h3: pm.querySelectorAll('h3').length,
                strong: pm.querySelectorAll('strong, b').length,
                ul: pm.querySelectorAll('ul').length,
                li: pm.querySelectorAll('li').length,
                blockquote: pm.querySelectorAll('blockquote').length,
                hr: pm.querySelectorAll('hr').length,
                a: pm.querySelectorAll('a[href]').length,
                has_raw_asterisks: txt.includes('**'),
                has_raw_hashes: /(?:^|\\n)##+\\s/.test(txt),
                has_raw_pipes: txt.includes('|') || txt.includes('｜')
            };
        }"""
    )


def save_note_draft(
    title: str,
    body: str,
    headless: bool = True,
    screenshot_name: str = "note_draft_latest.png",
) -> str:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # タイトルからもパイプ記号や不要な太字記号を除去
    clean_title = title.replace("**", "").replace("｜", " ― ").replace("|", " ― ").strip()

    log(f"note下書き保存開始: 『{clean_title}』 (元文字数={len(body)}, profile={PROFILE_DIR})")
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 1000},
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
                        loc.fill(clean_title)
                        title_filled = True
                        log(f"タイトル入力完了 ({sel})")
                        break
                except Exception:
                    continue
            if not title_filled:
                raise RuntimeError("noteタイトル欄が見つかりません。")

            time.sleep(0.5)

            # 本文入力（リッチテキストHTMLペースト）
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

            paste_rich_markdown_into_note(page, body)
            page.wait_for_timeout(1200)

            dom_stats = verify_rich_dom_elements(page)
            log(f"エディタリッチDOM検証: {dom_stats}")
            if dom_stats.get("len", 0) < 50:
                raise RuntimeError(f"本文がエディタに反映されませんでした (stats={dom_stats})")

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

            # スクリーンショット撮影時、記事冒頭の見出し・太字・リストが見える位置へスクロールして撮影
            page.evaluate("window.scrollTo(0, 180)")
            page.wait_for_timeout(500)
            shot_path = LOGS_DIR / screenshot_name
            page.screenshot(path=str(shot_path), full_page=False)
            log(f"note下書き保存完了: {draft_url} (screenshot: {shot_path})")
            return draft_url
        finally:
            browser.close()


def main():
    ap = argparse.ArgumentParser(description="note下書き自動保存（公開はしない）")
    ap.add_argument("--draft-file", "--draft-json", dest="draft_file", type=str, default=None, help="daily_*_note.json または .md のパス")
    ap.add_argument("--headless", dest="headless", action="store_true", default=False, help="ヘッドレスで実行（macOS Keychain制限によりCookie復号不可の場合あり）")
    ap.add_argument("--no-headless", dest="headless", action="store_false", help="通常のChromeウィンドウで実行（デフォルト・推奨）")
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
