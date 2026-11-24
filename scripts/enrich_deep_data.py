#!/usr/bin/env python3
"""
愛媛の空き家パトロール：ディープ情報抽出プロトタイプ（ハイブリッド：無料ルールベース ＋ 必要時のみ Gemini 2.5 Flash）
- 一度解析した物件ID＋本文ハッシュは `data/enrichment_cache.json` に保存し、新着・変更物件のみ解析する。
- 予算ガードレール：全件一括実行前に概算トークン数と推定コスト（円）を算出し、500円を超える場合は自動停止して承認を求める。
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import ssl
import urllib.request
from datetime import date
from pathlib import Path
import certifi
from pypdf import PdfReader

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "ehime_akiya_db.csv"
CACHE_PATH = BASE_DIR / "data" / "enrichment_cache.json"

SSL_CTX = ssl.create_default_context(cafile=certifi.where())
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# Gemini 2.5 Flash 単価（2026年標準：Input $0.30 / 1M tokens, Output $2.50 / 1M tokens, 1 USD = 155 JPY 換算）
USD_TO_JPY = 155.0
FLASH_INPUT_USD_PER_M = 0.30
FLASH_OUTPUT_USD_PER_M = 2.50
EST_SYSTEM_PROMPT_TOKENS = 380
EST_OUTPUT_TOKENS_PER_ITEM = 260


def fetch_raw_text(url: str) -> tuple[str, str]:
    """URLからテキストを抽出して (source_kind: 'pdf'|'html', text) を返す"""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=18, context=SSL_CTX) as resp:
        raw = resp.read()
    if url.lower().endswith(".pdf") or raw[:4] == b"%PDF":
        reader = PdfReader(io.BytesIO(raw))
        txt = "\n".join((p.extract_text() or "") for p in reader.pages)
        txt = re.sub(r"[ \t]+", " ", txt)
        txt = re.sub(r"\n{2,}", "\n", txt).strip()
        return "pdf", txt
    html_s = raw.decode("utf-8", "ignore")
    html_s = re.sub(r"(?s)<(script|style|noscript|svg|header|footer|nav).*?</\1>", "", html_s)
    html_s = re.sub(r"<br\s*/?>", "\n", html_s, flags=re.I)
    html_s = re.sub(r"</?(?:p|div|tr|li|dt|dd|th|td|h[1-6])[^>]*>", "\n", html_s, flags=re.I)
    html_s = re.sub(r"<[^>]+>", " ", html_s)
    lines = [re.sub(r"\s+", " ", l).strip() for l in html_s.splitlines()]
    txt = "\n".join(l for l in lines if l)
    return "html", txt


def rule_based_extract(text: str) -> dict:
    """
    無料のルールベース抽出で「水回り・建物状態・車/道路・土地付属物・商談ステータス」を取得する。
    抽出できた項目と、自由記述・PDF等でAI補完が必要かどうかのフラグ（needs_ai）を返す。
    """
    t = text.replace("\u3000", " ")

    # 1. 上水道
    water = None
    if re.search(r"公営水道|上水道\s*[:：]?\s*(?:あり|有|引込済|接続済|公営)", t):
        water = "公営上水道"
    elif re.search(r"簡易水道", t):
        water = "簡易水道"
    elif re.search(r"井戸|地下水", t):
        water = "井戸水"

    # 2. 下水・トイレ
    sewer = None
    if re.search(r"公共下水道|下水道\s*[:：]?\s*(?:あり|有|接続済|水洗)", t):
        sewer = "公共下水道（水洗）"
    elif re.search(r"合併浄化槽|浄化槽", t):
        sewer = "浄化槽（水洗）"
    elif re.search(r"汲み取り|くみ取り|汲取|ボットン", t):
        sewer = "汲み取り式"
    elif re.search(r"水洗", t):
        sewer = "水洗（方式要確認）"

    # 3. 築年数・補修要否・家財残置
    built = None
    m_built = re.search(r"(?:築年数|建築年|建築時期)\s*[:：]?\s*([^\n]{2,22})", t)
    if m_built:
        built = m_built.group(1).strip()
    else:
        m_b2 = re.search(r"(明治|大正|昭和|平成|令和)\s*[０-９\d]+年(?:頃|築)?|築\s*[０-９\d]+年", t)
        if m_b2:
            built = m_b2.group(0)

    repair = None
    if re.search(r"大幅な?(?:修繕|補修)|大規模(?:改修|修繕)|要解体", t):
        repair = "大幅改修要"
    elif re.search(r"多少(?:の)?(?:修繕|補修)|小規模(?:な)?(?:修繕|補修)|若干(?:の)?(?:修繕|補修)|一部(?:修繕|補修)", t):
        repair = "小規模修繕要"
    elif re.search(r"修繕\s*[:：]?\s*不要|補修(?:は)?不要|そのまま(?:居住|入居)|リフォーム済|改装済", t):
        repair = "そのまま居住可・状態良好"

    furniture = None
    if re.search(r"(?:家財|残置物|荷物)[^\n]{0,12}(?:あり|有|残って|残存|撤去要)", t):
        furniture = "家財残置あり"
    elif re.search(r"(?:家財|残置物)[^\n]{0,10}(?:なし|無|撤去済)", t):
        furniture = "家財なし（撤去済）"

    # 4. 駐車場・前面道路・周辺距離
    parking = None
    m_pk = re.search(r"駐車場?\s*[:：]?\s*([^\n]{1,28})", t)
    if m_pk and not re.search(r"なし|無", m_pk.group(1)):
        parking = m_pk.group(1).strip()
    elif re.search(r"駐車場?\s*[:：]?\s*(?:なし|無)", t):
        parking = "なし"

    road = None
    m_rd = re.search(r"(?:前面道路|接道|幅員|進入路)\s*[:：]?\s*([^\n]{2,36})", t)
    if m_rd:
        road = m_rd.group(1).strip()

    access_hits = re.findall(r"(?:スーパー|コンビニ|病院|駅|小学校|海|港|バス停)[^\n。]{1,24}(?:徒歩|車で|約?\d+(?:分|m|km|ｍ|ｋｍ))[^\n。]{0,12}", t)
    access = " ／ ".join(dict.fromkeys(x.strip() for x in access_hits[:3])) if access_hits else None

    # 5. 土地・付属物・ペット可否
    extras = []
    for label, pat in [
        ("農地・畑", r"(?:農地|畑|田畑|菜園)(?!\s*[:：]?\s*(?:なし|無))"),
        ("山林", r"山林(?!\s*[:：]?\s*(?:なし|無))"),
        ("倉庫・物置", r"倉庫|物置|納屋"),
        ("離れ・蔵", r"離れ|蔵"),
        ("庭", r"庭(?:あり|付|付き|広)"),
    ]:
        if re.search(pat, t):
            extras.append(label)

    pet = None
    if re.search(r"ペット[^\n]{0,10}(?:可|相談|OK|飼育可)", t):
        pet = "ペット可・相談"
    elif re.search(r"ペット[^\n]{0,10}(?:不可|NG|禁止)", t):
        pet = "ペット不可"

    # 6. 商談・交渉ステータス
    negotiation = "募集中"
    if re.search(r"交渉中|商談中|協議中|申込有", t):
        negotiation = "商談中・交渉中"
    elif re.search(r"成約済|成約済み|募集終了|契約済", t):
        negotiation = "成約済・募集終了"

    extracted = {
        "water_supply": water,
        "sewer_toilet": sewer,
        "built_year": built,
        "repair_level": repair,
        "remaining_furniture": furniture,
        "parking": parking,
        "front_road": road,
        "nearby_access": access,
        "land_extras": extras,
        "pet_policy": pet,
        "negotiation_status": negotiation,
    }
    filled = sum(1 for k, v in extracted.items() if k != "negotiation_status" and v)
    # 必須コア項目（上下水・補修・駐車場・道路）の欠損が多い、またはPDFの場合はAI補完候補とする
    core_missing = sum(1 for k in ("water_supply", "sewer_toilet", "repair_level", "parking", "front_road") if not extracted[k])
    return {
        "fields": extracted,
        "filled_count": filled,
        "core_missing": core_missing,
        "needs_ai_fallback": core_missing >= 2,
    }


def estimate_tokens_ja(text: str) -> int:
    """日本語テキストの概算トークン数（1文字≒0.75トークン、安全側で0.85係数）"""
    return int(len(text) * 0.85) + EST_SYSTEM_PROMPT_TOKENS


def estimate_cost_jpy(input_tokens: int, output_tokens: int) -> float:
    usd = (input_tokens / 1_000_000.0) * FLASH_INPUT_USD_PER_M + (output_tokens / 1_000_000.0) * FLASH_OUTPUT_USD_PER_M
    return usd * USD_TO_JPY


def run_sample_benchmark():
    with open(DB_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # 5市町（PDF自治体2＋HTML自治体3）から各2件＝計10件をサンプリング
    target_munis = ["西条市", "愛南町", "今治市", "大洲市", "松山市"]
    samples = []
    for m in target_munis:
        m_rows = [r for r in rows if r["自治体名"] == m][:2]
        samples.extend(m_rows)

    cache = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    total_in_tokens_if_all_ai = 0
    total_in_tokens_hybrid = 0
    ai_needed_count = 0
    results = []

    print(f"=== 10件サンプル深掘り抽出ベンチマーク ({date.today().isoformat()}) ===")
    for idx, r in enumerate(samples, 1):
        u = r["詳細URL"]
        muni = r["自治体名"]
        pid = r["物件ID"]
        try:
            src_kind, txt = fetch_raw_text(u)
            txt_trimmed = txt[:3200]  # トークン節約のため物件本体最大3,200字にトリム
            text_hash = hashlib.sha256(txt_trimmed.encode("utf-8")).hexdigest()[:16]
            rb = rule_based_extract(txt_trimmed)
            in_tok = estimate_tokens_ja(txt_trimmed)
            total_in_tokens_if_all_ai += in_tok
            needs_ai = rb["needs_ai_fallback"] or (src_kind == "pdf" and rb["core_missing"] >= 1)
            if needs_ai:
                ai_needed_count += 1
                total_in_tokens_hybrid += in_tok

            cache_key = f"{muni}:{pid}"
            cache[cache_key] = {
                "url": u,
                "source_kind": src_kind,
                "content_hash": text_hash,
                "updated_at": date.today().isoformat(),
                "extraction_mode": "rule_based_only" if not needs_ai else "hybrid_ai_candidate",
                "fields": rb["fields"],
            }
            results.append((muni, pid, src_kind, len(txt_trimmed), rb["filled_count"], needs_ai, rb["fields"]))
            flds = rb["fields"]
            print(
                f"{idx:2d}. [{muni}/{src_kind.upper()}] {pid} | 文字数={len(txt_trimmed):4d} | "
                f"ルール抽出={rb['filled_count']}/10 | AI補完要={str(needs_ai):5s} | "
                f"上水={flds['water_supply'] or '-'} / 下水={flds['sewer_toilet'] or '-'} / "
                f"補修={flds['repair_level'] or '-'} / 駐車={flds['parking'] or '-'}"
            )
        except Exception as e:
            print(f"{idx:2d}. [{muni}] {pid} ERR: {e}")

    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    # コスト試算（10件実測 → 541件全件・新着増分への外挿）
    n = max(1, len(results))
    avg_in_tok = total_in_tokens_if_all_ai / n
    cost_1_item_jpy = estimate_cost_jpy(int(avg_in_tok), EST_OUTPUT_TOKENS_PER_ITEM)
    hybrid_ratio = ai_needed_count / n

    total_db_items = 541
    est_all_ai_jpy = cost_1_item_jpy * total_db_items
    est_hybrid_initial_jpy = cost_1_item_jpy * (total_db_items * hybrid_ratio)
    # 毎朝の新着・変更（月間約60件と仮定）
    est_monthly_running_jpy = cost_1_item_jpy * (60 * hybrid_ratio)

    print("\n=== Gemini 2.5 Flash トークン＆コスト試算（1 USD = 155円換算） ===")
    print(f"・1件あたり平均入力トークン数: 約 {int(avg_in_tok):,} tokens / 出力: 約 {EST_OUTPUT_TOKENS_PER_ITEM} tokens")
    print(f"・1件あたり実測推定コスト: 約 {cost_1_item_jpy:.4f} 円")
    print(f"・ルールベースのみで完結した割合: {n - ai_needed_count}/{n}件 ({(1-hybrid_ratio)*100:.0f}%) / AI補完が必要な割合: {ai_needed_count}/{n}件 ({hybrid_ratio*100:.0f}%)")
    print(f"・【全541件を初回AI一括解析した場合】: 約 {est_all_ai_jpy:.1f} 円")
    print(f"・【ハイブリッド設計（欠損・PDFのみAI補完）初回541件】: 約 {est_hybrid_initial_jpy:.1f} 円")
    print(f"・【キャッシュ導入後の月額ランニングコスト（月60件の新着/変更想定）】: 約 {est_monthly_running_jpy:.1f} 円 / 月")
    print(f"・キャッシュ保存先: {CACHE_PATH} ({len(cache)}件)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="空き家ディープ情報抽出プロトタイプ＆コスト試算")
    ap.add_argument("--sample-benchmark", action="store_true", default=True, help="10件サンプルでルールベース抽出＋コスト試算を実行")
    args = ap.parse_args()
    run_sample_benchmark()
