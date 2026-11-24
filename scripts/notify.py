#!/usr/bin/env python3
"""
愛媛の空き家パトロール：差分検知＋note下書き保存完了時のメール通知スクリプト
※ 自身のディレクトリの .env のみを参照する（他パイプラインとは完全分離）。
"""

import argparse
import csv
import json
import re
import smtplib
import ssl
import sys
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
import certifi

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "data" / "ehime_akiya_db.csv"
CHANGE_HISTORY_PATH = BASE_DIR / "data" / "change_history.csv"
DRAFTS_DIR = BASE_DIR / "drafts"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_local_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        env[k] = v
    return env


def extract_sns_section(md_text: str) -> str:
    m = re.search(r"## 1\. X・Threads投稿案[^\n]*\n([\s\S]*?)\n---", md_text)
    if m:
        return m.group(1).strip()
    return md_text.strip()


def build_email_content(target_date: str) -> tuple[str, str]:
    ymd = target_date.replace("-", "")
    md_path = DRAFTS_DIR / f"daily_{ymd}.md"
    json_path = DRAFTS_DIR / f"daily_{ymd}_note.json"

    if not md_path.exists():
        raise FileNotFoundError(f"ドラフトファイルが見つかりません: {md_path}")

    md_text = md_path.read_text(encoding="utf-8")
    sns_section = extract_sns_section(md_text)

    new_cnt = 0
    chg_cnt = 0
    closed_cnt = 0
    if json_path.exists():
        meta = json.loads(json_path.read_text(encoding="utf-8"))
        new_cnt = int(meta.get("new_count", 0))
        chg_cnt = int(meta.get("price_changed_count", 0))
        closed_cnt = int(meta.get("closed_count", 0))

    # 当日分の変動履歴またはCSVレコードから簡易リストを構築
    today_changes = []
    if CHANGE_HISTORY_PATH.exists():
        with open(CHANGE_HISTORY_PATH, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("検知日") == target_date:
                    today_changes.append(r)

    if today_changes and (new_cnt == 0 and chg_cnt == 0 and closed_cnt == 0):
        new_cnt = sum(1 for r in today_changes if r.get("変動種別") == "新着")
        chg_cnt = sum(1 for r in today_changes if r.get("変動種別") in ("値下げ", "価格改定"))
        closed_cnt = sum(1 for r in today_changes if r.get("変動種別") == "募集終了")

    total = new_cnt + chg_cnt + closed_cnt
    subject = f"【愛媛空き家パトロール】本日の変動 {total}件｜新着{new_cnt}・値下げ{chg_cnt}・終了{closed_cnt}（note下書き保存完了）"

    simple_lines = []
    for i, r in enumerate(today_changes, 1):
        layout = r.get("間取り") or "-"
        ctype = r.get("変動種別") or "-"
        if ctype == "新着":
            price_info = r.get("新価格") or "要相談"
        elif ctype in ("値下げ", "価格改定"):
            price_info = f"{r.get('旧価格')}→{r.get('新価格')} ({r.get('価格差')})"
        else:
            price_info = f"最終価格:{r.get('旧価格')} (経過:{r.get('経過日数')})"
        simple_lines.append(
            f"{i}. [{ctype}] {r.get('自治体名')} {r.get('物件ID')}｜{price_info}｜間取り:{layout}\n"
            f"   公式URL: {r.get('詳細URL')}"
        )
    simple_list_str = "\n".join(simple_lines) if simple_lines else "（※詳細はnote下書きまたは添付ドラフトをご確認ください）"

    body = f"""愛媛の空き家パトロール 日次レポート（{target_date}）

■ 本日の検知件数
・合計：{total}件（新着 {new_cnt}件 / 値下げ・価格改定 {chg_cnt}件 / 募集終了 {closed_cnt}件）

■ 検知物件の簡易リスト
{simple_list_str}

■ note記事の編集・公開
以下の下書き一覧から該当記事を開き、「=== ここから先はメンバーシップ限定（月額1,280円） ===」の位置に有料/メンバーシップ限定ラインを設定し、「【地元民の本音メモ（追記欄）】」を1行足して公開してください！
https://note.com/notes

============================================================
■ スマホからそのままコピペできる X・Threads 投稿ドラフト全文
============================================================

{sns_section}
"""
    return subject, body


def send_gmail(smtp_user: str, app_password: str, to_addrs: list[str], subject: str, body: str):
    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.set_content(body)

    ctx = ssl.create_default_context(cafile=certifi.where())
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as server:
        server.login(smtp_user, app_password)
        server.send_message(msg)


def main():
    ap = argparse.ArgumentParser(description="差分検知・note下書き保存完了時のメール通知")
    ap.add_argument("--date", type=str, default=date.today().isoformat(), help="対象日付 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="送信せず内容のみ表示")
    args = ap.parse_args()

    env = load_local_env(ENV_PATH)
    smtp_user = env.get("SMTP_USER", "")
    app_pw = env.get("SMTP_APP_PASSWORD", "").replace(" ", "")
    to_raw = env.get("NOTIFY_TO_EMAIL", "")
    to_addrs = [x.strip() for x in to_raw.split(",") if x.strip()]

    if not args.dry_run and (not smtp_user or not app_pw or not to_addrs):
        log("通知スキップ（.env の SMTP_USER / SMTP_APP_PASSWORD / NOTIFY_TO_EMAIL が未設定です）")
        return

    subject, body = build_email_content(args.date)
    if args.dry_run:
        log(f"[DRY-RUN] Subject: {subject}")
        print(body[:1200])
        return

    send_gmail(smtp_user, app_pw, to_addrs, subject, body)
    log(f"通知メール送信完了 -> {', '.join(to_addrs)} ({subject})")


if __name__ == "__main__":
    main()
