#!/usr/bin/env python3
"""
data/ehime_akiya_db.csv と config/ehime_akiya_sources.json から
Web表示用の軽量JSON（web/data/site_data.json）を生成するスクリプト。
"""

import csv
import json
import re
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "ehime_akiya_db.csv"
CHANGE_HISTORY_PATH = BASE_DIR / "data" / "change_history.csv"
SOURCES_PATH = BASE_DIR / "config" / "ehime_akiya_sources.json"
OUT_PATH = BASE_DIR / "web" / "data" / "site_data.json"

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


def parse_sale_man(r: dict):
    if r.get("種別") == "賃貸":
        return None
    p = (
        (r.get("価格") or "")
        .replace(",", "")
        .replace("，", "")
        .translate(str.maketrans("０１２３４５６７８９．", "0123456789."))
    )
    if "売買" in p:
        p = p.split("売買", 1)[1]
    elif "売却" in p:
        p = p.split("売却", 1)[1].split("賃貸", 1)[0]
    if r.get("物件ID") == "#157" and "1.850" in p:
        return 1850.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*万\s*円?", p)
    if m and float(m.group(1)) > 0:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*円", p)
    if m and int(m.group(1)) > 0:
        return int(m.group(1)) / 10000.0
    return None


def parse_rent_yen(r: dict):
    if r.get("種別") != "賃貸":
        return None
    p = (
        (r.get("価格") or "")
        .replace(",", "")
        .replace("，", "")
        .translate(str.maketrans("０１２３４５６７８９．", "0123456789."))
    )
    if "賃貸（月額）" in p:
        p = p.split("賃貸（月額）", 1)[1]
    elif "賃借" in p:
        p = p.split("賃借", 1)[1].split("売買", 1)[0]
    m = re.search(r"(\d+)万(\d+)千\s*円", p)
    if m:
        return int(m.group(1)) * 10000 + int(m.group(2)) * 1000
    m = re.search(r"(\d+(?:\.\d+)?)\s*万\s*円?", p)
    if m and float(m.group(1)) > 0:
        return int(float(m.group(1)) * 10000)
    m = re.search(r"(\d+)\s*円", p)
    if m and int(m.group(1)) > 0:
        return int(m.group(1))
    return None


def has_farm(r: dict) -> bool:
    notes = r.get("特記事項") or ""
    return bool(
        re.search(r"畑|農地|菜園", notes)
        and not re.search(r"畑[：:]\s*なし|田畑・菜園\s*/|田畑・菜園$", notes)
        and "要解体" not in notes
    )


def has_sea(r: dict) -> bool:
    txt = (r.get("特記事項") or "") + (r.get("地区名") or "")
    return bool(
        (re.search(r"海|オーシャン|ビーチ|港|釣|瀬戸内", txt) or r.get("自治体名") == "上島町" or "島" in (r.get("地区名") or ""))
        and "要解体" not in txt
        and "空き地" not in txt
    )


def has_parking(r: dict) -> bool:
    notes = r.get("特記事項") or ""
    return bool(
        re.search(r"駐車場[：:]?\s*(?:有|あり|\d+台|軽)|駐車スペース|ガレージ|車庫", notes)
        and "駐車場:なし" not in notes
        and "駐車場:無" not in notes
    )


def has_good_cond(r: dict) -> bool:
    notes = r.get("特記事項") or ""
    return bool(re.search(r"修繕[:：]不要|修繕不要|補修は不要|リフォーム済|改装済|すぐに入居|すぐに生活", notes))


def is_under_100(r: dict, sale_val) -> bool:
    if sale_val is None or sale_val > 100:
        return False
    txt = (r.get("特記事項") or "") + (r.get("地区名") or "")
    return not bool(re.search(r"建物なし|空き地|売地|要解体", txt))


def feature_score(r: dict) -> tuple:
    """注目物件ピックアップ用の優先順位スコア"""
    st = r.get("ステータス") or ""
    is_recent = 1 if (st == "新着" or st.startswith("価格改定")) else 0
    dt = r.get("取得日") or ""
    sale_v = parse_sale_man(r)
    pts = 0
    if is_under_100(r, sale_v):
        pts += 3
    if has_farm(r):
        pts += 2
    if has_good_cond(r):
        pts += 2
    if has_sea(r):
        pts += 1
    if r.get("間取り"):
        pts += 1
    if not re.search(r"要解体|建物なし|空き地", (r.get("特記事項") or "") + (r.get("地区名") or "")):
        pts += 2
    return (is_recent, dt, pts)


def fmt_man(v: float) -> str:
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.1f}"


def main():
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    raw_rows = []
    if DB_PATH.exists():
        with open(DB_PATH, encoding="utf-8-sig") as f:
            raw_rows = list(csv.DictReader(f))

    # 同一物件（詳細URL または 自治体名+物件ID+種別）は最新の行だけを代表として採用
    latest_map = {}
    for r in raw_rows:
        k = (r["自治体名"], r["物件ID"], r["種別"]) if r.get("物件ID") else r["詳細URL"]
        latest_map[k] = r
    active_rows = list(latest_map.values())

    cutoff_7d = (date.today() - timedelta(days=7)).isoformat()
    recent_7d = sum(
        1
        for r in raw_rows
        if (r.get("取得日") or "") >= cutoff_7d
        and (
            (r.get("ステータス") or "") == "新着"
            or str(r.get("ステータス") or "").startswith("価格改定")
            or str(r.get("ステータス") or "").startswith("値下げ")
        )
    )

    # 直近7日間の変動履歴（件数のみ集計し、個別URLや値下げ額は無料Web用JSONには含めない）
    change_7d_total = {"new_count": 0, "price_down_count": 0, "closed_count": 0}
    change_7d_by_muni = {}
    if CHANGE_HISTORY_PATH.exists():
        with open(CHANGE_HISTORY_PATH, encoding="utf-8-sig") as f:
            for ch in csv.DictReader(f):
                d_str = (ch.get("検知日") or "")[:10]
                if d_str < cutoff_7d:
                    continue
                muni = ch.get("自治体名") or ""
                ctype = ch.get("変動種別") or ""
                m_bucket = change_7d_by_muni.setdefault(
                    muni, {"new_count": 0, "price_down_count": 0, "closed_count": 0}
                )
                if ctype == "新着":
                    change_7d_total["new_count"] += 1
                    m_bucket["new_count"] += 1
                elif ctype in ("値下げ", "価格改定"):
                    change_7d_total["price_down_count"] += 1
                    m_bucket["price_down_count"] += 1
                elif ctype == "募集終了":
                    change_7d_total["closed_count"] += 1
                    m_bucket["closed_count"] += 1

    all_sale_vals = [v for v in (parse_sale_man(r) for r in active_rows) if v is not None]
    all_rent_vals = [v for v in (parse_rent_yen(r) for r in active_rows) if v is not None]

    by_muni = {}
    for r in active_rows:
        by_muni.setdefault(r["自治体名"], []).append(r)

    munis_out = []
    for src in sources:
        m = src["municipality"]
        rs = by_muni.get(m, [])
        sale_vals = [v for v in (parse_sale_man(r) for r in rs) if v is not None]
        rent_cnt = sum(1 for r in rs if r.get("種別") == "賃貸")
        sale_cnt = len(rs) - rent_cnt

        # 無料Webサイトでは各市町の代表物件サンプルを最大3件までに制限
        sorted_rs = sorted(rs, key=feature_score, reverse=True)[:3]
        featured = []
        for r in sorted_rs:
            featured.append({
                "id": r.get("物件ID") or "-",
                "status": r.get("ステータス") or "募集中",
                "district": r.get("地区名") or m,
                "kind": r.get("種別") or "売買",
                "price": r.get("価格") or "要相談",
                "layout": r.get("間取り") or "",
                "built": r.get("築年数") or "",
                "land_area": r.get("土地面積") or "",
                "bldg_area": r.get("建物面積") or "",
                "notes": (r.get("特記事項") or "")[:120],
                "url": r.get("詳細URL") or src["list_url"],
            })

        munis_out.append({
            "name": m,
            "region": get_region(m),
            "count": len(rs),
            "sale_count": sale_cnt,
            "rent_count": rent_cnt,
            "price_stats": {
                "min_man": min(sale_vals) if sale_vals else None,
                "max_man": max(sale_vals) if sale_vals else None,
                "median_man": round(statistics.median(sale_vals), 1) if sale_vals else None,
            },
            "tags": {
                "under_100": sum(1 for r in rs if is_under_100(r, parse_sale_man(r))),
                "farm": sum(1 for r in rs if has_farm(r)),
                "sea": sum(1 for r in rs if has_sea(r)),
                "parking": sum(1 for r in rs if has_parking(r)),
                "good_cond": sum(1 for r in rs if has_good_cond(r)),
            },
            "recent_changes_7d": change_7d_by_muni.get(
                m, {"new_count": 0, "price_down_count": 0, "closed_count": 0}
            ),
            "featured": featured,
            "list_url": src["list_url"],
            "source_type": src.get("source_type", "html"),
            "source_notes": src.get("notes", ""),
        })

    out_data = {
        "summary": {
            "total_count": len(active_rows),
            "municipality_total": len(sources),
            "covered_municipalities": sum(1 for x in munis_out if x["count"] > 0),
            "sale_count": sum(1 for r in active_rows if r.get("種別") != "賃貸"),
            "rent_count": sum(1 for r in active_rows if r.get("種別") == "賃貸"),
            "sale_min_man": min(all_sale_vals) if all_sale_vals else None,
            "sale_max_man": max(all_sale_vals) if all_sale_vals else None,
            "sale_median_man": round(statistics.median(all_sale_vals), 1) if all_sale_vals else None,
            "rent_min_yen": min(all_rent_vals) if all_rent_vals else None,
            "rent_max_yen": max(all_rent_vals) if all_rent_vals else None,
            "rent_median_yen": int(statistics.median(all_rent_vals)) if all_rent_vals else None,
            "recent_7days_count": recent_7d,
            "recent_changes_7d": change_7d_total,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "region_counts": {
                "東予": sum(x["count"] for x in munis_out if x["region"] == "東予"),
                "中予": sum(x["count"] for x in munis_out if x["region"] == "中予"),
                "南予": sum(x["count"] for x in munis_out if x["region"] == "南予"),
            },
        },
        "municipalities": munis_out,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes)")
    print("Summary:", json.dumps(out_data["summary"], ensure_ascii=False))
    for x in munis_out:
        print(f"  {x['name']} ({x['region']}): {x['count']}件 (売買{x['sale_count']}/賃貸{x['rent_count']})")


if __name__ == "__main__":
    main()
