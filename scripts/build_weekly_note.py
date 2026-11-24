#!/usr/bin/env python3
"""
愛媛の空き家パトロール：週次ダイジェスト記事生成スクリプト（日曜まとめ配信用）
`data/change_history.csv` から直近7日間の「新着・値下げ（価格改定）・募集終了」を集計し、
「無料公開パート（市町別件数サマリー）＋ メンバーシップ限定パート（全件詳細・値下げ差額・成約スピード・地元民の本音メモ）」
の構成で `drafts/weekly_YYYYMMDD.md` および `drafts/weekly_YYYYMMDD_note.json` を出力する。
"""

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CHANGE_HISTORY_PATH = BASE_DIR / "data" / "change_history.csv"
DB_PATH = BASE_DIR / "data" / "ehime_akiya_db.csv"
DRAFTS_DIR = BASE_DIR / "drafts"

TOYO = {"四国中央市", "新居浜市", "西条市", "今治市", "上島町"}
CHUYO = {"松山市", "伊予市", "東温市", "久万高原町", "松前町", "砥部町"}
NANYO = {"大洲市", "八幡浜市", "西予市", "宇和島市", "内子町", "伊方町", "松野町", "鬼北町", "愛南町"}


def get_region(muni: str) -> str:
    if muni in TOYO:
        return "東予"
    if muni in CHUYO:
        return "中予"
    if muni in NANYO:
        return "南予"
    return "その他"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_recent_changes(target_date: date, days: int = 7) -> list[dict]:
    start_date = target_date - timedelta(days=days - 1)
    rows = []
    if CHANGE_HISTORY_PATH.exists():
        with open(CHANGE_HISTORY_PATH, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                d_str = (r.get("検知日") or "")[:10]
                if not d_str:
                    continue
                try:
                    d = date.fromisoformat(d_str)
                except ValueError:
                    continue
                if start_date <= d <= target_date:
                    rows.append(r)
    return rows


def build_weekly_draft(target_date: date, days: int = 7) -> tuple[Path, Path]:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    today_str = target_date.isoformat()
    start_str = (target_date - timedelta(days=days - 1)).isoformat()
    ymd = today_str.replace("-", "")
    md_path = DRAFTS_DIR / f"weekly_{ymd}.md"
    json_path = DRAFTS_DIR / f"weekly_{ymd}_note.json"

    change_rows = load_recent_changes(target_date, days=days)
    new_cnt = sum(1 for c in change_rows if c.get("変動種別") == "新着")
    down_cnt = sum(1 for c in change_rows if c.get("変動種別") in ("値下げ", "価格改定"))
    closed_cnt = sum(1 for c in change_rows if c.get("変動種別") == "募集終了")

    title = (
        f"【週間まとめ {start_str}〜{today_str}】愛媛の空き家パトロール｜"
        f"新着{new_cnt}件・値下げ{down_cnt}件・募集終了{closed_cnt}件"
    )

    # 市町×変動種別集計（無料公開パート用：URLや値下げ額は伏せ、市町名と件数のみ開示）
    by_muni_type = Counter()
    muni_order = []
    for c in change_rows:
        m = c.get("自治体名", "")
        t = c.get("変動種別", "")
        if m and m not in muni_order:
            muni_order.append(m)
        by_muni_type[(m, t)] += 1

    parts = []
    for m in muni_order:
        for t in ("新着", "値下げ", "価格改定", "募集終了"):
            cnt = by_muni_type.get((m, t), 0)
            if cnt > 0:
                parts.append(f"{m}で{t}{cnt}件")

    if parts:
        summary_sentence = f"今週（{start_str}〜{today_str}）は、{'、'.join(parts)}を確認しました。"
    else:
        summary_sentence = f"今週（{start_str}〜{today_str}）の巡回では大きな変動（新着・値下げ・募集終了）はありませんでした。"

    free_lines = [
        "【無料公開パート】",
        "こんにちは、「愛媛の空き家パトロール」です。",
        f"今週7日間（{start_str}〜{today_str}）の愛媛県内全20市町・空き家バンク巡回ダイジェストをお届けします。",
        "",
        "■ 今週の巡回サマリー（市町別件数）",
        summary_sentence,
        f"・今週の新着物件：{new_cnt}件",
        f"・今週の値下げ・価格改定：{down_cnt}件",
        f"・今週の募集終了（成約・取下げ）：{closed_cnt}件",
        "（※無料パートでは市町名と件数のみ公開しています。具体的な物件URL・旧価格→新価格の値下げ幅・募集終了までの経過日数・地元民の本音メモはメンバーシップ限定パートにて公開中です）",
        "",
        "■ 愛媛県全20市町の空き家相場マップ＆代表物件サンプル（無料Webサイト）",
        "https://ehime-akiya-patrol.pages.dev/",
        "",
    ]

    member_lines = [
        "=== ここから先はメンバーシップ限定（月額1,280円） ===",
        "",
        "【メンバーシップ限定パート】",
        "いつもメンバーシップをご利用いただきありがとうございます。",
        f"今週（{start_str}〜{today_str}）に検知した「新着」「値下げ（価格改定）」「募集終了（成約スピード）」の全件リストと、公開サイトでは書けない地元民の本音メモをお届けします。",
        "",
    ]

    sections = [
        ("新着", "■ 1. 今週の新着物件まとめ"),
        ("値下げ", "■ 2. 今週の値下げ・価格改定物件（旧価格→新価格・差額）"),
        ("募集終了", "■ 3. 今週の募集終了・成約確認物件（初回取得からの経過日数）"),
    ]

    for key_type, heading in sections:
        if key_type == "値下げ":
            subset = [c for c in change_rows if c.get("変動種別") in ("値下げ", "価格改定")]
        else:
            subset = [c for c in change_rows if c.get("変動種別") == key_type]

        member_lines.append(f"{heading}（{len(subset)}件）")
        member_lines.append("")
        if not subset:
            member_lines.append("・該当なし")
            member_lines.append("")
            continue

        for c in subset:
            muni = c.get("自治体名", "")
            region = get_region(muni)
            pid = c.get("物件ID") or "IDなし"
            area = c.get("地区名") or "地区要確認"
            layout = c.get("間取り") or "間取り要確認"
            feat = c.get("特記事項") or "特記なし"
            url = c.get("詳細URL") or ""
            det_date = c.get("検知日") or today_str

            if c.get("変動種別") == "新着":
                price_line = f"  価格：{c.get('新価格') or '要相談'}（検知日：{det_date}）"
            elif c.get("変動種別") in ("値下げ", "価格改定"):
                price_line = (
                    f"  価格推移：{c.get('旧価格')} → {c.get('新価格')}（差額：{c.get('価格差')}｜検知日：{det_date}・初回取得からの経過：{c.get('経過日数')}）"
                )
            else:
                price_line = (
                    f"  最終確認価格：{c.get('旧価格') or '-'}（初回取得日：{c.get('初回取得日')} → 募集終了までの経過日数：{c.get('経過日数')}｜検知日：{det_date}）"
                )

            member_lines.extend([
                f"・【{c.get('変動種別')}】[{region}] {muni} {pid}（{area}）",
                price_line,
                f"  間取り・特徴：{layout} ／ {feat}",
                f"  公式詳細URL：{url}",
                "> 【地元民の本音メモ（追記欄）】道幅・周辺環境・割安度：",
                "",
            ])

    member_lines.extend([
        "---",
        "■ メンバーシップ会員特典のご案内",
        "【メンバーシップ会員特典：現地代理下見が通常10,000円→8,000円（2,000円OFF）＆優先対応】",
        "気になる物件が見つかった際、遠方からいきなり現地へ行く前に、地元民が「周辺の道幅・日当たり・騒音・近隣スーパーや病院までの実測感」を写真30枚以上＋動画で調査する現地代理下見レポートを、会員様限定で毎回2,000円OFF（税込8,000円・優先日程調整）にて承っております。",
        "お申し込みはWebサイトの専用フォーム（https://ehime-akiya-patrol.pages.dev/#inspection-section）より「noteメンバーシップ会員」にチェックを入れてご依頼ください。",
        "",
        "※本記事は自動パトロールで抽出した一次情報をもとに作成しています。最新の募集状況や内見申込は各自治体の公式窓口にてご確認ください。",
        "",
        "#空き家バンク #古民家暮らし #地方移住 #愛媛移住 #二拠点生活",
    ])

    note_body = "\n".join(free_lines + member_lines)

    table_lines = [
        "### 週間変動履歴一覧テーブル",
        "| 検知日 | 変動種別 | エリア | 自治体 | 物件ID | 旧価格 | 新価格 | 価格差 | 経過日数 | 公式URL |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in change_rows:
        table_lines.append(
            f"| {c.get('検知日', '-')} | {c.get('変動種別', '-')} | {get_region(c.get('自治体名', ''))} | {c.get('自治体名', '-')} | {c.get('物件ID', '-')} | {c.get('旧価格') or '-'} | {c.get('新価格') or '-'} | {c.get('価格差') or '-'} | {c.get('経過日数') or '-'} | {c.get('詳細URL', '-')} |"
        )

    md_full = (
        f"# {title}\n\n"
        f"{note_body}\n\n"
        f"{chr(10).join(table_lines)}\n"
    )
    md_path.write_text(md_full, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "title": title,
                "body": note_body,
                "date": today_str,
                "period_start": start_str,
                "period_end": today_str,
                "new_count": new_cnt,
                "price_changed_count": down_cnt,
                "closed_count": closed_cnt,
                "md_path": str(md_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return md_path, json_path


def main():
    ap = argparse.ArgumentParser(description="直近7日間の変動履歴から週次noteダイジェスト記事を生成")
    ap.add_argument("--date", type=str, default=date.today().isoformat(), help="集計基準日 YYYY-MM-DD（デフォルト: 今日）")
    ap.add_argument("--days", type=int, default=7, help="集計対象日数（デフォルト: 7日間）")
    ap.add_argument("--save-note", action="store_true", help="生成後にそのまま note_drafter.py を呼び出してnote下書き保存まで実行")
    args = ap.parse_args()

    target_date = date.fromisoformat(args.date)
    md_path, json_path = build_weekly_draft(target_date, days=args.days)
    log(f"週次ダイジェスト生成完了: {md_path} / {json_path}")
    print(f"WEEKLY_MD={md_path}")
    print(f"WEEKLY_JSON={json_path}")

    if args.save_note:
        drafter = BASE_DIR / "scripts" / "note_drafter.py"
        cmd = [sys.executable, str(drafter), "--draft-file", str(json_path), "--screenshot-name", f"weekly_{target_date.strftime('%Y%m%d')}_draft.png"]
        log(f"note下書き保存を実行します: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
