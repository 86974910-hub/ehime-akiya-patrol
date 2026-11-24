#!/usr/bin/env python3
"""
愛媛の空き家パトロール：週次ダイジェスト記事生成スクリプト（日曜まとめ配信用）
`data/change_history.csv` から直近7日間の「新着・値下げ（価格改定）・募集終了」を集計し、
パイプ記号（| / ｜）や擬似テーブルを一切使わず、note標準見出し（## / ###）・太字・箇条書き・引用ブロックを用いた
「地元民が親身に語りかける雑誌特集スタイル」で `drafts/weekly_YYYYMMDD.md` および `drafts/weekly_YYYYMMDD_note.json` を出力する。
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


def _human_feature_summary(feat: str) -> str:
    clean = (feat or "").replace("|", "／").replace("｜", "・").strip()
    if not clean:
        return "自治体公式ページにて外観写真や詳細スペックが公開されています。改修の要否や周辺環境をあわせてご確認ください。"
    return f"{clean}。実際に検討される際は、前面道路の道幅や日当たりも含めてチェックしておくと安心です。"


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
        f"【週間まとめ {start_str}〜{today_str}】愛媛の空き家パトロール ― "
        f"新着{new_cnt}件・値下げ{down_cnt}件・募集終了{closed_cnt}件の動向レポート"
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
        summary_sentence = f"今週7日間（{start_str}〜{today_str}）の巡回では、**{'、'.join(parts)}**の動きを確認しました。"
    else:
        summary_sentence = f"今週7日間（{start_str}〜{today_str}）の巡回では、大きな変動（新着・値下げ・募集終了）はありませんでした。"

    free_lines = [
        "こんにちは、愛媛県の移住・古民家情報をお届けする「愛媛の空き家パトロール」です。",
        "",
        f"平日は仕事や家事で忙しく、県内20市町の空き家バンクを毎日チェックするのはなかなか手が回らないという方に向けて、今週7日間（{start_str}〜{today_str}）の変動ダイジェストをまとめました。",
        "",
        "## 今週の空き家バンク巡回サマリー（無料公開パート）",
        "",
        summary_sentence,
        "",
        f"- **今週の新着物件**：**{new_cnt}件**",
        f"- **今週の値下げ・価格改定**：**{down_cnt}件**",
        f"- **今週の募集終了（成約・取下げ）**：**{closed_cnt}件**",
        "",
        "無料公開パートでは、今週どの市町にどれくらい動きがあったかという全体サマリーをお伝えしています。具体的な物件ページURL、旧価格から新価格への値下げ幅、募集終了までの経過日数（成約スピード）、そして公開サイトでは書きにくい「地元民の本音メモ」は、この下のメンバーシップ限定パートで詳しく解説しています。",
        "",
        "まずは愛媛県全20市町の募集件数やエリア別の価格相場を地図で見比べたいという方は、毎日自動更新している無料Webマップからご覧ください。",
        "",
        "- **愛媛県全20市町の空き家相場マップ＆代表物件サンプル**：[Webマップを開いて市町別の相場を見る](https://ehime-akiya-patrol.pages.dev/)",
        "",
    ]

    member_lines = [
        "---",
        "",
        "=== ここから先はメンバーシップ限定（月額1,280円） ===",
        "",
        "## 今週の変動物件・全件詳細レポート（メンバーシップ限定）",
        "",
        "いつもメンバーシップで応援していただき、本当にありがとうございます。",
        "",
        f"ここからは、今週（{start_str}〜{today_str}）に検知した「新着」「値下げ（価格改定）」「募集終了（成約スピード）」の全件リストと、地元在住者ならではのチェックポイントをお届けします。",
        "",
    ]

    sections = [
        (
            "新着",
            "## 1. 今週の新着物件まとめ",
            "今週新しく空き家バンクに登場した物件です。補修不要の物件や畑・駐車場付きの物件は問い合わせが集中しやすいため、気になるエリアがあれば早めに公式ページをチェックしておくのが安心です。",
        ),
        (
            "値下げ",
            "## 2. 今週の値下げ・価格改定物件（旧価格から新価格への推移）",
            "今週価格が引き下げられた（または条件が改定された）物件です。浮いた購入費用を水回りや屋根の改修費に回せるため、トータル予算を抑えたい方にとって狙い目となります。",
        ),
        (
            "募集終了",
            "## 3. 今週の募集終了・成約スピード記録（掲載経過日数）",
            "今週、自治体サイト上で募集終了（または成約・取下げ）となった物件です。「どんな価格帯・条件の家が何日くらいで決まっているか」という相場観をつかむ材料としてご活用ください。",
        ),
    ]

    for key_type, heading, intro_desc in sections:
        if key_type == "値下げ":
            subset = [c for c in change_rows if c.get("変動種別") in ("値下げ", "価格改定")]
        else:
            subset = [c for c in change_rows if c.get("変動種別") == key_type]

        member_lines.append(f"{heading}（{len(subset)}件）")
        member_lines.append("")
        if not subset:
            member_lines.append("今週の巡回では、該当する物件はありませんでした。")
            member_lines.append("")
            continue

        member_lines.append(intro_desc)
        member_lines.append("")

        for c in subset:
            muni = c.get("自治体名", "")
            region = get_region(muni)
            pid = c.get("物件ID") or "番号なし"
            area = (c.get("地区名") or f"{muni}内").replace("|", "／").replace("｜", "・")
            layout = (c.get("間取り") or "公式ページ参照").replace("|", "／").replace("｜", "・")
            feat_note = _human_feature_summary(c.get("特記事項", ""))
            url = c.get("詳細URL") or ""
            det_date = c.get("検知日") or today_str

            if c.get("変動種別") == "新着":
                new_p = (c.get("新価格") or "価格要相談").replace("|", "／").replace("｜", "・")
                member_lines.extend([
                    f"### 【{muni}・{area}】物件番号 {pid}（新着・{new_p}）",
                    "",
                    f"- **エリア**：{muni}（{region}エリア） {area}",
                    f"- **募集価格**：**{new_p}**（検知日：{det_date}）",
                    f"- **間取り**：{layout}",
                    f"- **物件のポイント**：{feat_note}",
                    f"- **公式ページ**：[自治体空き家バンクの詳細ページを見る]({url})",
                    "",
                    "> 【地元民の本音メモ（追記欄）】道幅・周辺環境・割安度：",
                    "",
                ])
            elif c.get("変動種別") in ("値下げ", "価格改定"):
                old_p = (c.get("旧価格") or "-").replace("|", "／").replace("｜", "・")
                new_p = (c.get("新価格") or "-").replace("|", "／").replace("｜", "・")
                diff_p = (c.get("価格差") or "価格改定").replace("|", "／").replace("｜", "・")
                member_lines.extend([
                    f"### 【{muni}・{area}】物件番号 {pid}（{old_p} → {new_p}）",
                    "",
                    f"- **エリア**：{muni}（{region}エリア） {area}",
                    f"- **価格の推移**：{old_p} から **{new_p}** へ改定（変動幅：**{diff_p}**）",
                    f"- **検知日・経過日数**：{det_date} 検知（初回取得日 {c.get('初回取得日')} から {c.get('経過日数')} 経過）",
                    f"- **間取り**：{layout}",
                    f"- **物件のポイント**：{feat_note}",
                    f"- **公式ページ**：[自治体空き家バンクの詳細ページを見る]({url})",
                    "",
                    "> 【地元民の本音メモ（追記欄）】道幅・周辺環境・割安度：",
                    "",
                ])
            else:
                old_p = (c.get("旧価格") or "価格記録なし").replace("|", "／").replace("｜", "・")
                member_lines.extend([
                    f"### 【{muni}・{area}】物件番号 {pid}（募集終了・掲載 {c.get('経過日数')}）",
                    "",
                    f"- **エリア**：{muni}（{region}エリア） {area}",
                    f"- **最終確認価格**：{old_p}",
                    f"- **成約・終了までのスピード**：初回取得日 {c.get('初回取得日')} から **{c.get('経過日数')}** で募集終了を確認（検知日：{det_date}）",
                    f"- **間取り・特徴**：{layout}（{feat_note}）",
                    f"- **参考URL**：[掲載されていた公式ページを確認する]({url})",
                    "",
                    "> 【地元民の本音メモ（追記欄）】道幅・周辺環境・割安度：",
                    "",
                ])

    member_lines.extend([
        "---",
        "",
        "## メンバーシップ会員特典：現地代理下見レポート 2,000円OFF",
        "",
        "気になる物件が見つかった際、いきなり遠方から飛行機やレンタカーを手配して現地へ行くのは、時間も交通費も大きな負担になります。",
        "",
        "そこで当メンバーシップの会員様には、愛媛在住の運営者があなたに代わって現地へ足を運ぶ「現地代理下見レポート」を、**通常10,000円のところ会員価格8,000円（税込・毎回2,000円OFF）＆優先日程調整**で承っております。",
        "",
        "- **会員特典価格**：1件 **8,000円（税込）**（通常 10,000円から2,000円OFF・1回のご依頼で月額会費以上にお得になります）",
        "- **調査内容**：外観・屋根・基礎の目視写真30枚以上、前面道路の幅員（普通車で離合・駐車できるか）、日当たりや隣家との距離感、最寄りスーパーや病院までの実測距離、周辺ノーカット動画（3〜5分）",
        "- **お申し込み窓口**：[愛媛の空き家パトロール 現地代理下見フォームから相談する](https://ehime-akiya-patrol.pages.dev/#inspection-section)",
        "",
        "本記事は各自治体の公式空き家バンク一次情報をもとに作成しています。最新の募集状況や内見申込については、各自治体の公式窓口にてご確認ください。",
        "",
        "#空き家バンク #古民家暮らし #地方移住 #愛媛移住 #二拠点生活",
    ])

    note_body = "\n".join(free_lines + member_lines)
    md_full = f"# {title}\n\n{note_body}\n"

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
        cmd = [
            sys.executable,
            str(drafter),
            "--draft-file",
            str(json_path),
            "--screenshot-name",
            f"weekly_{target_date.strftime('%Y%m%d')}_draft.png",
        ]
        log(f"note下書き保存を実行します: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
