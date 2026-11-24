#!/usr/bin/env python3
"""
愛媛の空き家パトロール：日次巡回・自己修復・差分抽出・ドラフト生成スクリプト
"""

import argparse
import csv
import html
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
import certifi
from pypdf import PdfReader

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "ehime_akiya_sources.json"
DB_PATH = BASE_DIR / "data" / "ehime_akiya_db.csv"
CHANGE_HISTORY_PATH = BASE_DIR / "data" / "change_history.csv"
DRAFTS_DIR = BASE_DIR / "drafts"
LOGS_DIR = BASE_DIR / "logs"

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
SSL_CTX = ssl.create_default_context(cafile=certifi.where())
CLOSED = re.compile(r"交渉中|協議中|商談中|成約済|成約済み|募集終了")

FIELDS = [
    "取得日", "ステータス", "自治体名", "地区名", "種別", "価格",
    "間取り", "築年数", "土地面積", "建物面積", "特記事項",
    "物件ID", "詳細URL", "掲載元",
]

CHANGE_HISTORY_FIELDS = [
    "検知日", "変動種別", "自治体名", "地区名", "物件ID",
    "旧価格", "新価格", "価格差", "初回取得日", "経過日数",
    "間取り", "特記事項", "詳細URL",
]

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


def get_bytes(u: str, timeout: int = 25) -> bytes:
    err = None
    for _ in range(2):
        try:
            req = urllib.request.Request(u, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
                return resp.read()
        except Exception as e:
            err = e
            time.sleep(0.5)
    log(f"WARN: fetch failed {u} ({err})")
    return b""


def get(u: str, encoding: str = "utf-8") -> str:
    b = get_bytes(u)
    return b.decode(encoding, "ignore") if b else ""


def strip_tags(s: str) -> str:
    s = re.sub(r"(?s)<(script|style|noscript|svg).*?</\1>", "", s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def clean_text(s: str) -> str:
    s = re.sub(r"(?s)<(script|style|noscript|svg).*?</\1>", "", s)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = html.unescape(s).replace("\u200b", "").replace("\xa0", " ")
    return "\n".join(l.strip() for l in s.split("\n") if l.strip())


def links(s: str, base: str):
    out = []
    for m in re.finditer(r'(?s)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', s):
        t = strip_tags(m.group(2))
        out.append((urllib.parse.urljoin(base, html.unescape(m.group(1))), t))
    return out


def parse_th_td(s: str) -> dict:
    kv = {}
    for m in re.finditer(r"(?s)<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", s):
        k = strip_tags(m.group(1)).replace(" ", "").replace("　", "")
        v = strip_tags(m.group(2))
        if k and k not in kv:
            kv[k] = v
    return kv


def parse_all_rows(s: str):
    out = []
    for m in re.finditer(r"(?s)<tr[^>]*>(.*?)</tr>", s):
        cells = [strip_tags(c) for c in re.findall(r"(?s)<t[hd][^>]*>.*?</t[hd]>", m.group(1))]
        if cells:
            out.append(cells)
    return out


def parse_kv_pairs(s: str) -> dict:
    kv = {}
    for cells in parse_all_rows(s):
        if len(cells) in (2, 4):
            for i in range(0, len(cells), 2):
                k = cells[i].replace(" ", "").replace("　", "")
                v = cells[i + 1]
                if k and k not in kv:
                    kv[k] = v
    return kv


def parse_dl_pairs(s: str) -> dict:
    kv = {}
    for k, v in re.findall(r"(?s)<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", s):
        kk = strip_tags(k).replace(" ", "").replace("　", "")
        vv = strip_tags(v)
        if kk and kk not in kv:
            kv[kk] = vv
    return kv


def rx(pat: str, t: str, n: int = 80) -> str:
    m = re.search(pat, t)
    return m.group(1).strip()[:n] if m else ""


def self_heal_url(muni: str, old_url: str, hint_pattern: str) -> str:
    """list_urlが404または構造変化した際、同一ドメインのトップから空き家一覧URLを再探索する"""
    parsed = urllib.parse.urlparse(old_url)
    root = f"{parsed.scheme}://{parsed.netloc}/"
    log(f"SELF-HEAL: {muni} の一覧URL再探索を開始 ({root})")
    candidates = [root]
    if parsed.path.startswith("/site/"):
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2:
            candidates.insert(0, f"{root}{parts[0]}/{parts[1]}/")
    visited = set()
    for base_u in candidates:
        s = get(base_u)
        if not s:
            continue
        for h, txt in links(s, base_u):
            if urllib.parse.urlparse(h).netloc != parsed.netloc:
                continue
            if h in visited or any(x in h for x in (".pdf", ".jpg", ".png", "#")):
                continue
            if re.search(r"akiya|vacant|residence|bukken|空き家|うちこ屋", h + " " + txt, re.I):
                visited.add(h)
                probe = get(h)
                if probe and re.search(hint_pattern, probe):
                    log(f"SELF-HEAL SUCCESS: {muni} -> {h}")
                    return h
    return ""


# ----------------- 自治体別スクレイパー -----------------

def scrape_imabari(list_url: str):
    out = []
    seen = set()
    base = list_url.split("/vacant-house")[0] if "/vacant-house" in list_url else "https://iju-imabari.jp"
    for cat, kind in (("baibai-bukken", "売買"), ("chintai-bukken", "賃貸")):
        for p in range(1, 16):
            u = f"{base}/category/akiya-bank/{cat}/" + (f"page/{p}/" if p > 1 else "")
            s = get(u)
            if not s:
                break
            L = [h for h, _ in links(s, u) if re.search(r"/akiya-bank/[^/]+/$", h) and "/category/" not in h]
            new = [h for h in dict.fromkeys(L) if h not in seen]
            if not new:
                break
            for h in new:
                seen.add(h)
                d = get(h)
                if not d:
                    continue
                t = clean_text(d)
                if CLOSED.search(t[:400]):
                    continue
                kv = parse_th_td(d)
                pid = rx(r"【物件No\.([^】]+)】", t)
                area = rx(r"エリア\n([^\n]+)", t)
                loc = kv.get("所在地", "").replace("愛媛県今治市", "").strip()
                price = kv.get("価格") or kv.get("賃料") or ""
                notes = [x for x in (kv.get("備考", ""), f"駐車場:{kv['駐車場']}" if kv.get("駐車場") else "", kv.get("こだわりポイント", "")) if x]
                out.append(dict(
                    自治体名="今治市", 地区名=(area + " " + loc).strip(), 種別=kind, 価格=price,
                    間取り=kv.get("間取り", ""), 築年数=kv.get("築年月", ""),
                    土地面積=kv.get("土地面積", ""), 建物面積=kv.get("建物面積", ""),
                    特記事項=" / ".join(notes)[:180], 物件ID=pid, 詳細URL=h, 掲載元="いまばり空き家バンク"
                ))
                time.sleep(0.15)
    return out


def scrape_kamijima(list_url: str):
    s = get(list_url)
    urls = list(dict.fromkeys(re.findall(r"https://kamijima-life\.jp/residences/\d+/", s)))
    if not urls:
        return []

    def fetch(h):
        d = get(h)
        if not d:
            return None
        title = rx(r"<title>(.*?)</title>", d, 120)
        if CLOSED.search(title) or CLOSED.search(strip_tags(d)[:500]):
            return None
        kv = parse_dl_pairs(d)
        price_raw = kv.get("価格", "")
        kind = "賃貸" if "賃貸" in price_raw and "売買" not in price_raw else "売買"
        area_raw = kv.get("広さ", "")
        return dict(
            自治体名="上島町", 地区名=kv.get("所在地", ""), 種別=kind,
            価格=re.sub(r"^(?:売買|賃貸)\s*", "", price_raw).strip(),
            間取り=kv.get("間取り", ""), 築年数=kv.get("築年数", ""),
            土地面積=rx(r"敷地面積\s*([\d,\.]+\s*㎡)", area_raw),
            建物面積=rx(r"延床面積\s*([\d,\.]+\s*㎡)", area_raw),
            特記事項=kv.get("構造", "")[:180],
            物件ID=kv.get("物件番号", "").replace(" ", ""), 詳細URL=h, 掲載元="瀬戸内かみじまライフ"
        )

    with ThreadPoolExecutor(max_workers=6) as ex:
        return [r for r in ex.map(fetch, urls) if r]


def scrape_saijo(list_url: str):
    s = get(list_url)
    tasks = []
    seen = set()
    for m in re.finditer(r"(?s)<tr[^>]*>(.*?)</tr>", s):
        tds = re.findall(r"(?s)<td[^>]*>(.*?)</td>", m.group(1))
        if len(tds) < 2 or CLOSED.search(strip_tags(tds[1])):
            continue
        pdf_rel = rx(r'href="([^"]+\.pdf)"', tds[0], 200)
        if not pdf_rel:
            continue
        pdf_url = urllib.parse.urljoin(list_url, pdf_rel)
        if pdf_url in seen:
            continue
        seen.add(pdf_url)
        info_txt = strip_tags(tds[0])
        m_id = re.search(r"(?:No\.?|№)\s*(\d+)\s*([^【\s]+)\s*【([^】]+)】", info_txt)
        pid = f"No.{m_id.group(1)}" if m_id else ""
        loc = m_id.group(2).replace("西条市", "").strip() if m_id else ""
        kind = m_id.group(3).strip() if m_id else "売却"
        summary = re.sub(r"詳細情報\s*\[PDF.*", "", re.sub(r".*?】\s*", "", info_txt)).strip()
        tasks.append((pid, loc, kind, summary, pdf_url))

    def fetch(task):
        pid, loc, kind, summary, pdf_url = task
        pb = get_bytes(pdf_url)
        price = land = bldg = built = ""
        notes = [summary] if summary else []
        if pb:
            try:
                pt = PdfReader(io.BytesIO(pb)).pages[0].extract_text() or ""
                pm = re.search(r"希望価格\s*(.+)", pt)
                if pm:
                    price = re.sub(r"\s+", " ", pm.group(1)).strip()
                lm = re.search(r"土地\s*([\d,\.]+\s*㎡)", pt)
                if lm:
                    land = lm.group(1).replace(" ", "")
                bm = re.search(r"延床面積\s*([\d,\.]+\s*㎡)", pt)
                if bm:
                    bldg = bm.group(1).replace(" ", "")
                ym = re.search(r"建築年\s*([^\n]+)", pt)
                if ym and ym.group(1).strip() != "年":
                    built = ym.group(1).strip()
                rm = re.search(r"■([^\s□■]*補修[^\s□■]*)", pt)
                if rm:
                    notes.append(rm.group(1))
                tm = re.search(r"特記事項\s*([\s\S]{1,100})", pt)
                if tm:
                    tn = re.sub(r"\s+", " ", tm.group(1)).strip()
                    if tn:
                        notes.append(tn)
            except Exception as e:
                log(f"WARN: Saijo PDF parse failed {pdf_url} ({e})")
        return dict(
            自治体名="西条市", 地区名=loc, 種別=kind, 価格=price, 間取り="", 築年数=built,
            土地面積=land, 建物面積=bldg, 特記事項=" / ".join(dict.fromkeys(x for x in notes if x))[:180],
            物件ID=pid, 詳細URL=pdf_url, 掲載元="西条市公式(PDF)"
        )

    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(fetch, tasks))


def scrape_shikokuchuo(list_url: str):
    out = []
    seen = set()
    base = list_url.rstrip("/") + "/"
    for p in (1, 2, 3):
        u = base + (f"page/{p}/" if p > 1 else "")
        s = get(u)
        if not s:
            break
        items = s.split('class="akiyabank_item"')
        if len(items) <= 1:
            break
        for it in items[1:]:
            if CLOSED.search(it[:450]):
                continue
            h3 = re.search(r'<h3[^>]*class="u-mb5"[^>]*>(.*?)</h3>', it)
            if not h3:
                continue
            m_id = re.search(r"(?:No\.?|№)\s*(\d+)\s*([^【]+)【([^】]+)】", strip_tags(h3.group(1)))
            if not m_id:
                continue
            pid = "No." + m_id.group(1)
            if pid in seen:
                continue
            seen.add(pid)
            loc, kind = m_id.group(2).strip(), m_id.group(3).strip()
            summary = strip_tags(rx(r'<p[^>]*class="u-mb15"[^>]*>(.*?)</p>', it, 200))
            pdf_rel = rx(r'href="([^"]+\.pdf)"', it, 300)
            pdf_url = urllib.parse.urljoin(u, pdf_rel) if pdf_rel else u
            price = land = bldg = built = ""
            notes = [summary] if summary else []
            if pdf_rel:
                pb = get_bytes(pdf_url)
                if pb:
                    try:
                        pt = PdfReader(io.BytesIO(pb)).pages[0].extract_text() or ""
                        pm = re.search(r"希望価格\s*(.+)", pt)
                        if pm:
                            price = re.sub(r"\s+", " ", pm.group(1)).strip()
                        lm = re.search(r"土地\s*([\d,\.]+\s*㎡)", pt)
                        if lm:
                            land = lm.group(1).replace(" ", "")
                        bm = re.search(r"延床面積\s*([\d,\.]+\s*㎡)", pt)
                        if bm:
                            bldg = bm.group(1).replace(" ", "")
                        ym = re.search(r"建築年\s*([^\n]+)", pt)
                        if ym and ym.group(1).strip() != "年":
                            built = ym.group(1).strip()
                        pkm = re.search(r"駐車場\s*([^\n□■]+)", pt)
                        if pkm and pkm.group(1).strip():
                            notes.append(f"駐車場:{pkm.group(1).strip()}")
                        rm = re.search(r"■([^\s□■]*補修[^\s□■]*)", pt)
                        if rm:
                            notes.append(rm.group(1))
                        tm = re.search(r"特記事項\s*([\s\S]{1,120})", pt)
                        if tm and re.sub(r"\s+", " ", tm.group(1)).strip():
                            notes.append(re.sub(r"\s+", " ", tm.group(1)).strip())
                    except Exception as e:
                        log(f"WARN: Shikokuchuo PDF failed {pdf_url} ({e})")
            out.append(dict(
                自治体名="四国中央市", 地区名=loc, 種別=kind, 価格=price, 間取り="", 築年数=built,
                土地面積=land, 建物面積=bldg, 特記事項=" / ".join(dict.fromkeys(x for x in notes if x))[:180],
                物件ID=pid, 詳細URL=pdf_url, 掲載元="四国のまんなか生活(PDF)"
            ))
    return out


def scrape_matsuyama(list_url: str):
    out = []
    s = get(list_url)
    seen = set()
    for h, _ in links(s, list_url):
        if not re.search(r"https://ritou-akiya\.com/house/[^/\"?#]+$", h) or h in seen:
            continue
        seen.add(h)
        d = get(h)
        if not d or CLOSED.search(strip_tags(d)[:400]):
            continue
        kv = parse_kv_pairs(d)
        kind = "賃貸" if "貸" in kv.get("利用種別", "") else "売買"
        out.append(dict(
            自治体名="松山市", 地区名=kv.get("島名・地区", "") or kv.get("所在地", ""),
            種別=kind, 価格=kv.get("価格", ""), 間取り=kv.get("間取り", ""),
            築年数=kv.get("築年", ""), 土地面積="", 建物面積=kv.get("延べ床", ""),
            特記事項=kv.get("間取り詳細", "")[:180], 物件ID=h.rsplit("/", 1)[-1],
            詳細URL=h, 掲載元="里島空き家バンク(松山市離島)"
        ))
    return out


def scrape_iyo(list_url: str):
    out = []
    s = get(list_url)
    seen = set()
    for h, tt in links(s, list_url):
        if "/akiya_bank/" not in h or h in seen or CLOSED.search(tt):
            continue
        seen.add(h)
        d = get(h)
        if not d or CLOSED.search(d[:800]):
            continue
        kv = parse_kv_pairs(d)
        kind = "賃貸" if "賃貸" in tt or "貸します" in tt else "売買"
        notes = [kv.get(k, "") for k in ("駐車場", "その他", "都市計画") if kv.get(k)]
        out.append(dict(
            自治体名="伊予市", 地区名=kv.get("所在地", "").replace("伊予市", "").strip(),
            種別=kind, 価格=kv.get("希望価格", ""), 間取り=kv.get("間取り", ""),
            築年数=kv.get("建築年", ""), 土地面積=kv.get("土地面積", ""),
            建物面積=kv.get("建物面積", ""), 特記事項=" / ".join(notes)[:180],
            物件ID=kv.get("登録番号", ""), 詳細URL=h, 掲載元="空き家サポート伊予"
        ))
    return out


def scrape_toon(list_url: str):
    out = []
    seen = set()
    base = list_url.rstrip("/")
    for cat_u, kind in ((f"{base}/selling/", "売買"), (f"{base}/lease/", "賃貸")):
        s = get(cat_u)
        for h, _ in links(s, cat_u):
            if not re.search(r"https://toon-iju\.com/akiya/\d+\.html", h) or h in seen:
                continue
            seen.add(h)
            d = get(h)
            if not d or CLOSED.search(strip_tags(d)[:500]):
                continue
            kv = parse_kv_pairs(d)
            out.append(dict(
                自治体名="東温市", 地区名=kv.get("所在地", "").replace("東温市", "").strip(),
                種別=kind, 価格=kv.get("価格", "") or kv.get("賃料", ""),
                間取り=kv.get("間取り", ""), 築年数=kv.get("築年数", "") or kv.get("建築年", ""),
                土地面積=kv.get("土地面積", ""), 建物面積=kv.get("建物面積", ""),
                特記事項=kv.get("特記事項（設備条件など）", "")[:180],
                物件ID=rx(r"/akiya/(\d+)\.html", h), 詳細URL=h, 掲載元="東温市移住ポータル"
            ))
    return out


def scrape_kumakogen(list_url: str):
    out = []
    s = get(list_url)
    seen = set()
    for h, lt in links(s, list_url):
        m = re.match(r"(【[^】]*】)?\s*(No\.?\s*[\d\-]+)\s*(久万高原町\S*)\s*(.*)", lt)
        if not (m and "/site/iju/" in h and h not in seen) or CLOSED.search(lt):
            continue
        seen.add(h)
        d = get(h)
        if not d or CLOSED.search(clean_text(d)[:400]):
            continue
        kv = parse_kv_pairs(d)
        loc = kv.get("所在地", m.group(3)).replace("久万高原町", "").strip()
        kind = "賃貸" if "賃貸" in kv.get("売却/賃貸", "") or "賃貸" in lt else "売買"
        notes = []
        if "建物なし" in lt:
            notes.append("建物なし(土地)")
        if m.group(1):
            notes.append("民間事業者所有")
        if kv.get("修繕の要否"):
            notes.append(f"修繕:{kv['修繕の要否']}")
        if kv.get("駐車場"):
            notes.append(f"駐車場:{kv['駐車場']}")
        ag = kv.get("農地・山林等", "")
        if any(x in ag for x in ("畑：", "田：", "山林：")) and not all(x in ag for x in ("畑：なし", "田：なし", "山林：なし")):
            notes.append(ag[:60])
        if kv.get("所有者様からのコメント"):
            notes.append(kv["所有者様からのコメント"][:80])
        out.append(dict(
            自治体名="久万高原町", 地区名=loc, 種別=kind, 価格=kv.get("希望価格", ""),
            間取り="", 築年数=kv.get("建築年", ""), 土地面積=kv.get("敷地面積", ""), 建物面積="",
            特記事項=" / ".join(notes)[:180], 物件ID=m.group(2).replace(" ", ""),
            詳細URL=h, 掲載元="久万高原町公式"
        ))
    return out


def scrape_uwajima(list_url: str):
    out = []
    s = get(list_url)
    for h in dict.fromkeys(h for h, _ in links(s, list_url) if re.search(r"/site/ijyu/akiya(bank)?\d+\.html", h)):
        d = get(h)
        if not d:
            continue
        t = clean_text(d)
        if CLOSED.search(t[:400]):
            continue
        trows = parse_all_rows(d)
        kv = parse_kv_pairs(d)
        loc = price = built = layout = ""
        for i, r in enumerate(trows):
            if len(r) >= 4 and "所在地" in r[0] and i + 1 < len(trows):
                if len(trows[i + 1]) >= 5:
                    loc, price, built, _, layout = trows[i + 1][:5]
                break
        pid_m = re.search(r"空き家バンク物件紹介（(\d+)\.", t) or re.search(r"akiya(?:bank)?(\d+)\.html", h)
        pid = f"No.{pid_m.group(1)}" if pid_m else ""
        kind = "賃貸" if ("賃貸" in price or ("賃貸" in kv.get("区分", "") and "売買" not in kv.get("区分", ""))) else "売買"
        bldg = re.sub(r"（1階.*", "", kv.get("建物面積（延べ床）", "") or kv.get("建物面積", "")).strip()
        notes = []
        for k, lb in (("駐車場", "駐車場"), ("補修要否", "補修"), ("畑", "畑"), ("庭・畑", "庭・畑")):
            if kv.get(k) and kv.get(k) != "なし":
                notes.append(f"{lb}:{kv[k]}")
        if kv.get("特記事項"):
            notes.append(kv["特記事項"])
        out.append(dict(
            自治体名="宇和島市", 地区名=loc, 種別=kind,
            価格=re.sub(r"^(?:売買|賃貸)\s*", "", price).strip(),
            間取り=layout or kv.get("間取り", ""), 築年数=built,
            土地面積=kv.get("土地面積", ""), 建物面積=bldg,
            特記事項=" / ".join(notes)[:180], 物件ID=pid, 詳細URL=h, 掲載元="宇和島市公式"
        ))
    return out


def scrape_yawatahama(list_url: str):
    seen = set()
    tasks = []
    base = list_url.rstrip("/") + "/"
    for p in range(1, 10):
        u = base + (f"page/{p}/" if p > 1 else "")
        s = get(u)
        if not s or "物件管理番号" not in s:
            break
        found_new = False
        for h, tt in links(s, u):
            if re.search(r"https://yawatahama-iju\.com/akiya/\d+/", h) and h not in seen:
                seen.add(h)
                found_new = True
                if not CLOSED.search(tt):
                    tasks.append((h, tt))
        if not found_new:
            break

    def fetch(task):
        h, tt = task
        d = get(h)
        if not d:
            return None
        kv = parse_kv_pairs(d)
        pid = rx(r"【物件管理番号】(\d+)", tt)
        kind = "賃貸" if "賃貸" in tt else "売買"
        price = rx(r"([\d,]+\s*万円(?:/月)?|[\d,]+\s*円/月)", tt)
        notes = [kv.get(k, "") for k in ("状態", "駐車場", "設備条件") if kv.get(k)]
        return dict(
            自治体名="八幡浜市", 地区名=rx(r"(まち暮らし|山暮らし|海暮らし)", tt) or "八幡浜市",
            種別=kind, 価格=price,
            間取り=rx(r"/\s*([0-9]+[SLDKR]+)", tt) or kv.get("間取り詳細", "")[:30],
            築年数=kv.get("築年月", ""), 土地面積=kv.get("土地面積", ""), 建物面積=kv.get("建物面積", ""),
            特記事項=" / ".join(notes)[:180], 物件ID=f"No.{pid}" if pid else "",
            詳細URL=h, 掲載元="八幡浜市移住ポータル"
        )

    with ThreadPoolExecutor(max_workers=6) as ex:
        return [r for r in ex.map(fetch, tasks) if r]


def scrape_ozu(list_url: str):
    out = []
    seen = set()
    base_search = list_url.split("?")[0]
    for tp in (11, 12, 21, 31, 32):
        u = f"{base_search}?type={tp}"
        s = get(u)
        for h, _ in links(s, u):
            if not re.search(r"/akiyabank/\d+-\d+-\d+\.html", h) or h in seen:
                continue
            seen.add(h)
            d = get(h)
            if not d or CLOSED.search(strip_tags(d)[:500]):
                continue
            kv = parse_kv_pairs(d)
            kind = "賃貸" if tp in (12, 32) else "売買"
            notes = [kv.get(k, "") for k in ("修繕・解体の要否", "車庫・駐車場", "田畑・菜園") if kv.get(k)]
            out.append(dict(
                自治体名="大洲市",
                地区名=(kv.get("地域名", "") + " " + kv.get("地区名", "") + " " + kv.get("所在地", "").replace("大洲市", "")).strip(),
                種別=kind, 価格=kv.get("売買価格") or kv.get("賃貸価格(月額)") or "",
                間取り="" if kv.get("間取り", "") == "間取り図のとおり" else kv.get("間取り", ""),
                築年数=kv.get("築年数", ""), 土地面積=kv.get("敷地(土地)面積", ""), 建物面積=kv.get("建物面積", ""),
                特記事項=" / ".join(notes)[:180], 物件ID=kv.get("物件番号", ""), 詳細URL=h, 掲載元="大洲市公式"
            ))
    return out


def scrape_uchiko(list_url: str):
    out = []
    s = get(list_url)
    seen = set()
    for h, _ in links(s, list_url):
        if not re.search(r"/teijyu-akiya/ijyu/akiya\d+\.html", h) or h in seen:
            continue
        seen.add(h)
        d = get(h)
        if not d or CLOSED.search(strip_tags(d)[:500]):
            continue
        kv = parse_kv_pairs(d)
        price = kv.get("売買価格") or kv.get("賃貸価格（月額）") or ""
        kind = "賃貸" if kv.get("賃貸価格（月額）") and not kv.get("売買価格") else "売買"
        notes = [kv.get(k, "") for k in ("修繕・解体の要否", "利用状況") if kv.get(k)]
        out.append(dict(
            自治体名="内子町", 地区名=(kv.get("エリア名", "") + " " + kv.get("所在地", "")).strip(),
            種別=kind, 価格=price,
            間取り="" if kv.get("間取り", "") in ("図面参照", "間取り図のとおり") else kv.get("間取り", ""),
            築年数=kv.get("建築年", ""), 土地面積=kv.get("敷地（土地）面積", ""),
            建物面積=kv.get("建物面積（延床）", ""), 特記事項=" / ".join(notes)[:180],
            物件ID=kv.get("物件番号", ""), 詳細URL=h, 掲載元="うちこ屋バンク"
        ))
    return out


def scrape_ikata(list_url: str):
    out = []
    s = get(list_url)
    seen = set()
    for h, _ in links(s, list_url):
        if not re.search(r"/akiya/iju-teiju/\d+\.html", h) or h in seen:
            continue
        seen.add(h)
        d = get(h)
        if not d or CLOSED.search(strip_tags(d)[:500]):
            continue
        kv = parse_kv_pairs(d)
        tp = kv.get("種別", "")
        kind = "賃貸" if "貸" in tp else "売買"
        out.append(dict(
            自治体名="伊方町", 地区名=(kv.get("地域", "") + " " + kv.get("所在地", "").replace("伊方町", "")).strip(),
            種別=kind, 価格=kv.get("金額", ""),
            間取り="" if kv.get("間取り", "") == "-" else kv.get("間取り", ""),
            築年数="" if kv.get("築年数", "") == "-" else kv.get("築年数", ""),
            土地面積=kv.get("土地面積", ""), 建物面積=kv.get("建物面積", ""),
            特記事項=tp, 物件ID=kv.get("物件番号", ""), 詳細URL=h, 掲載元="伊方町公式"
        ))
    return out


def scrape_matsuno(list_url: str):
    out = []
    s = get(list_url)
    seen = set()
    for h, tt in links(s, list_url):
        if re.search(r"/site/akiya/\d+\.html", h) and "空き家バンク" in tt and ("No" in tt or "Ｎｏ" in tt):
            if h in seen or CLOSED.search(tt):
                continue
            seen.add(h)
            d = get(h)
            if not d or CLOSED.search(clean_text(d)[:400]):
                continue
            kv = parse_kv_pairs(d)
            pid_m = re.search(r"(?:No|Ｎｏ)[,\s\.]*(\d+)", tt)
            pid = f"No.{pid_m.group(1)}" if pid_m else ""
            loc = kv.get("所在地", "").replace("松野町大字", "").replace("松野町", "").strip()
            if not loc:
                loc = rx(r"(?:No|Ｎｏ)[,\s]*\d+\s*([^\s（(]+)", tt, 25)
            kr = kv.get("賃貸・売却", "")
            kind = "賃貸" if "賃貸" in kr and "売買" not in kr and "売却" not in kr else ("売買・賃貸" if "賃貸" in kr and ("売買" in kr or "売却" in kr) else "売買")
            sa = kv.get("構造", "")
            notes = [kv.get(k, "") for k in ("付帯設備", "特記事項") if kv.get(k)]
            out.append(dict(
                自治体名="松野町", 地区名=loc, 種別=kind, 価格=kv.get("希望価格", ""),
                間取り="" if kv.get("間取り", "") == "間取り図のとおり" else kv.get("間取り", ""),
                築年数=kv.get("建築時期", ""),
                土地面積=rx(r"宅地面積\s*([\d,\.]+\s*(?:平方メートル|㎡))", sa),
                建物面積=rx(r"建築面積\s*([\d,\.]+\s*(?:平方メートル|㎡))", sa),
                特記事項=" / ".join(notes)[:180] or tt[:60], 物件ID=pid, 詳細URL=h, 掲載元="松野町公式"
            ))
    return out


def scrape_kihoku(list_url: str):
    out = []
    s = get(list_url)
    seen = set()
    for h, tt in links(s, list_url):
        if "/t_bukken/" in h and "価格" in tt and h not in seen:
            seen.add(h)
            d = get(h)
            kv = parse_kv_pairs(d)
            other = kv.get("その他", "")
            loc_head = rx(r"^(\S+)〔", tt)
            area_sub = kv.get("地区", rx(r"地区\s+(\S+地区)", tt))
            notes = [f"駐車場:{kv['駐車場']}"] if kv.get("駐車場") else []
            if other:
                notes.append(other[:140])
            out.append(dict(
                自治体名="鬼北町", 地区名=f"{loc_head}({area_sub})" if loc_head else area_sub,
                種別="売買", 価格=kv.get("価格", rx(r"価格\s+(\S+)", tt)),
                間取り=kv.get("間取り", rx(r"間取り\s+(\S+)", tt)),
                築年数=kv.get("築年数", rx(r"築年数\s+(\S+)", tt)),
                土地面積=rx(r"宅地\s*([\d,\.]+\s*平米)", other),
                建物面積=kv.get("面積", rx(r"面積\s+(\S+)", tt)),
                特記事項=" / ".join(notes)[:180], 物件ID=rx(r"〔(\d+)〕", tt),
                詳細URL=h, 掲載元="鬼北町空き家情報"
            ))
    return out


def scrape_ainan(list_url: str):
    out = []
    s = get(list_url)
    ai_pdfs = [p for p in dict.fromkeys(re.findall(r'files/[^"]+\.pdf', s)) if "daityou" in p or "_HP" in p]
    for p_rel in ai_pdfs:
        pdf_url = urllib.parse.urljoin(list_url, p_rel)
        pb = get_bytes(pdf_url)
        if not pb:
            continue
        try:
            pt = PdfReader(io.BytesIO(pb)).pages[0].extract_text() or ""
            fn_id = rx(r"/(\d{4})[^/]*\.pdf$", pdf_url, 10)
            raw_id = rx(r"登\s*録\s*番\s*号\s*(\d{3,4})", pt, 10)
            pid = fn_id or raw_id
            loc = rx(r"所\s*在\s*地\s*([^\n]+)", pt, 40).replace("愛南町", "").strip()
            if re.match(r"^\d{4}\s*分類$", loc):
                loc = ""
            loc = re.sub(r"\s+", " ", loc).strip()
            price = rx(r"([\d,]+\s*万(?:円)?(?:/月)?|[\d,]+\s*円/月)", pt, 30)
            built = rx(r"(築\s*\d+\s*年|昭和\s*[０-９\d]+\s*年|平成\s*[０-９\d]+\s*年|令和\s*[０-９\d]+\s*年)", pt, 20)
            land = rx(r"土地\s*([\d,\.]+)", pt, 20)
            if land:
                land += "㎡"
            kind = "賃貸" if "円/月" in price else "売買"
            out.append(dict(
                自治体名="愛南町", 地区名=loc, 種別=kind, 価格=price, 間取り="", 築年数=built,
                土地面積=land, 建物面積="", 特記事項=rx(r"(津波[^\n]+|駐車場[^\n]+)", pt, 80),
                物件ID=pid, 詳細URL=pdf_url, 掲載元="愛南町公式(PDF)"
            ))
        except Exception as e:
            log(f"WARN: Ainan PDF failed {pdf_url} ({e})")
    return out


SCRAPERS = {
    "今治市": (scrape_imabari, r"/akiya-bank/"),
    "上島町": (scrape_kamijima, r"/residences/\d+"),
    "西条市": (scrape_saijo, r"/uploaded/attachment/\d+\.pdf"),
    "四国中央市": (scrape_shikokuchuo, r"akiyabank_item"),
    "松山市": (scrape_matsuyama, r"/house/"),
    "伊予市": (scrape_iyo, r"/akiya_bank/\d+"),
    "東温市": (scrape_toon, r"/akiya/(?:selling|lease|\d+\.html)"),
    "久万高原町": (scrape_kumakogen, r"/site/iju/\d+\.html"),
    "宇和島市": (scrape_uwajima, r"/site/ijyu/akiya(?:bank)?\d+\.html"),
    "八幡浜市": (scrape_yawatahama, r"物件管理番号"),
    "大洲市": (scrape_ozu, r"/akiyabank/\d+-\d+-\d+\.html"),
    "内子町": (scrape_uchiko, r"/teijyu-akiya/ijyu/akiya\d+\.html"),
    "伊方町": (scrape_ikata, r"/akiya/iju-teiju/\d+\.html"),
    "松野町": (scrape_matsuno, r"/site/akiya/\d+\.html"),
    "鬼北町": (scrape_kihoku, r"/t_bukken/"),
    "愛南町": (scrape_ainan, r"_daityou\.pdf|_HP\.pdf"),
}


# ----------------- 差分判定・ドラフト生成 -----------------

def norm_price(p: str) -> str:
    s = (p or "").translate(str.maketrans("０１２３４５６７８９．，", "0123456789.,"))
    s = re.sub(r"\s+", "", s)
    return s


def parse_price_yen(p: str) -> float:
    """価格文字列を円単位の数値に変換する（比較・差額計算用）。不明時は 0.0"""
    s = norm_price(p).replace(",", "")
    if not s:
        return 0.0
    m_man = re.search(r"(\d+(?:\.\d+)?)万", s)
    if m_man:
        return float(m_man.group(1)) * 10000.0
    m_yen = re.search(r"(\d+(?:\.\d+)?)円", s)
    if m_yen:
        val = float(m_yen.group(1))
        return val
    return 0.0


def compute_price_diff(old_p: str, new_p: str) -> tuple[str, str]:
    """
    旧価格と新価格から (変動種別, 価格差文字列) を返す。
    値下げの場合は ('値下げ', '-50万円') 等、値上げや賃貸/売買変更等は ('価格改定', '+20万円' or '条件改定')。
    """
    old_val = parse_price_yen(old_p)
    new_val = parse_price_yen(new_p)
    if old_val > 0 and new_val > 0 and old_val != new_val:
        diff_yen = new_val - old_val
        sign = "-" if diff_yen < 0 else "+"
        abs_yen = abs(diff_yen)
        if abs_yen >= 10000:
            man = abs_yen / 10000.0
            man_str = f"{int(man):,}" if man.is_integer() else f"{man:.1f}"
            diff_str = f"{sign}{man_str}万円"
        else:
            diff_str = f"{sign}{int(abs_yen):,}円"
        change_type = "値下げ" if diff_yen < 0 else "価格改定"
        return change_type, diff_str
    return "価格改定", f"{old_p}→{new_p}"


def calc_elapsed_days(first_date_str: str, today_str: str) -> str:
    if not first_date_str:
        return "0日"
    try:
        d0 = date.fromisoformat(first_date_str[:10])
        d1 = date.fromisoformat(today_str[:10])
        return f"{max(0, (d1 - d0).days)}日"
    except Exception:
        return "0日"


def make_sns_post(r: dict) -> str:
    muni = r["自治体名"]
    area = r["地区名"].split()[0] if r["地区名"] else ""
    loc_tag = f"{muni}({area})" if area and area != muni else muni
    price = r["価格"] or "要相談"
    layout = f"・{r['間取り']}" if r["間取り"] else ""
    status = str(r.get("ステータス", ""))
    prefix = "【愛媛・空き家値下げ】" if ("値下げ" in status or "価格改定" in status) else "【愛媛・空き家速報】"
    tags = "#空き家バンク #古民家暮らし #地方移住 #愛媛移住"
    feat = (r.get("特記事項") or "").replace("\n", " ").strip()
    url = r["詳細URL"]

    # X仕様: URLは23文字換算で140字以内に収める
    fixed_head = f"{prefix}\n{loc_tag}・{r['種別']}{price}{layout}！\n"
    fixed_tail = f"\n詳細👇\n{url}\n{tags}"
    budget = 140 - len(fixed_head) - len(fixed_tail.replace(url, "X" * 23))
    if budget < 15:
        body_msg = "注目物件が登場！"
    else:
        feat_short = feat[:max(0, budget - 16)].rstrip(" /・。")
        cta = "気になる方はチェック！"
        body_msg = f"{feat_short}。{cta}" if feat_short else "移住や二拠点生活の候補にぜひチェック！"
        if len(body_msg) > budget:
            body_msg = body_msg[:budget - 1] + "…"
    return f"{fixed_head}{body_msg}{fixed_tail}"


def build_free_public_summary(change_rows: list[dict], today_str: str) -> list[str]:
    """
    無料公開パート：市町名と件数のみを開示し、物件URL・詳細・値下げ額は伏せる。
    """
    parts = []
    # 市町×変動種別で集計
    by_muni_type = Counter()
    muni_order = []
    for c in change_rows:
        m = c["自治体名"]
        t = c["変動種別"]
        if m not in muni_order:
            muni_order.append(m)
        by_muni_type[(m, t)] += 1

    for m in muni_order:
        for t in ("新着", "値下げ", "価格改定", "募集終了"):
            cnt = by_muni_type.get((m, t), 0)
            if cnt > 0:
                parts.append(f"{m}で{t}{cnt}件")

    summary_sentence = (
        f"本日は{'、'.join(parts)}を確認しました。"
        if parts
        else "本日の巡回では変動物件はありませんでした。"
    )

    return [
        "【無料公開パート】",
        "こんにちは、「愛媛の空き家パトロール」です。",
        f"{today_str}の愛媛県内全20市町・空き家バンク巡回サマリーをお届けします。",
        "",
        f"■ 本日の巡回サマリー",
        summary_sentence,
        "（※無料パートでは市町名と件数のみ公開しています。具体的な物件URL・値下げ額・成約までの経過日数・地元民の本音メモはメンバーシップ限定パートにて公開中です）",
        "",
        "■ 愛媛県全20市町の空き家相場マップ＆代表物件サンプル（無料Webサイト）",
        "https://ehime-akiya-patrol.pages.dev/",
        "",
    ]


def build_membership_section(change_rows: list[dict]) -> list[str]:
    """
    メンバーシップ限定パート：新着・値下げ・募集終了の全件詳細と【地元民の本音メモ】、会員特典案内を出力。
    """
    lines = [
        "=== ここから先はメンバーシップ限定（月額1,280円） ===",
        "",
        "【メンバーシップ限定パート】",
        "いつもメンバーシップをご利用いただきありがとうございます。",
        "本日検知した「新着」「値下げ（価格改定）」「募集終了（成約・取下げスピード）」の全件詳細データと、公開サイトでは書けない地元民の本音メモをお届けします。",
        "",
    ]

    sections = [
        ("新着", "■ 1. 本日の新着物件"),
        ("値下げ", "■ 2. 本日の値下げ・価格改定物件（旧価格→新価格・差額）"),
        ("募集終了", "■ 3. 本日の募集終了・成約確認物件（初回取得からの経過日数）"),
    ]

    for key_type, heading in sections:
        if key_type == "値下げ":
            subset = [c for c in change_rows if c["変動種別"] in ("値下げ", "価格改定")]
        else:
            subset = [c for c in change_rows if c["変動種別"] == key_type]

        lines.append(f"{heading}（{len(subset)}件）")
        lines.append("")
        if not subset:
            lines.append("・該当なし")
            lines.append("")
            continue

        for c in subset:
            muni = c["自治体名"]
            region = get_region(muni)
            pid = c["物件ID"] or "IDなし"
            area = c["地区名"] or "地区要確認"
            layout = c["間取り"] or "間取り要確認"
            feat = c["特記事項"] or "特記なし"
            url = c["詳細URL"]

            if c["変動種別"] == "新着":
                price_line = f"  価格：{c['新価格'] or '要相談'}（初回取得日：{c['初回取得日']}）"
            elif c["変動種別"] in ("値下げ", "価格改定"):
                price_line = (
                    f"  価格推移：{c['旧価格']} → {c['新価格']}（差額：{c['価格差']}｜初回取得日：{c['初回取得日']}・経過：{c['経過日数']}）"
                )
            else:
                price_line = (
                    f"  最終確認価格：{c['旧価格'] or '-'}（初回取得日：{c['初回取得日']} → 募集終了までの経過日数：{c['経過日数']}）"
                )

            lines.extend([
                f"・【{c['変動種別']}】[{region}] {muni} {pid}（{area}）",
                price_line,
                f"  間取り・特徴：{layout} ／ {feat}",
                f"  公式詳細URL：{url}",
                "> 【地元民の本音メモ（追記欄）】道幅・周辺環境・割安度：",
                "",
            ])

    lines.extend([
        "---",
        "■ メンバーシップ会員特典のご案内",
        "【メンバーシップ会員特典：現地代理下見が通常10,000円→8,000円（2,000円OFF）＆優先対応】",
        "遠方からいきなり現地へ行く前に、地元民が「周辺の道幅・日当たり・騒音・近隣スーパーや病院までの実測感」を写真30枚以上＋動画で調査する現地代理下見レポートを、会員様限定で毎回2,000円OFF（税込8,000円・優先日程調整）にて承っております。",
        "お申し込みはWebサイトの専用フォーム（https://ehime-akiya-patrol.pages.dev/#inspection-section）より「noteメンバーシップ会員」にチェックを入れてご依頼ください。",
        "",
        "※本記事は自動パトロールで抽出した一次情報をもとに作成しています。最新の募集状況や内見申込は各自治体の公式窓口にてご確認ください。",
        "",
        "#空き家バンク #古民家暮らし #地方移住 #愛媛移住",
    ])
    return lines


def generate_drafts(diff_rows: list, change_rows: list, today_str: str):
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    ymd = today_str.replace("-", "")
    md_path = DRAFTS_DIR / f"daily_{ymd}.md"
    json_path = DRAFTS_DIR / f"daily_{ymd}_note.json"

    new_cnt = sum(1 for c in change_rows if c["変動種別"] == "新着")
    down_cnt = sum(1 for c in change_rows if c["変動種別"] in ("値下げ", "価格改定"))
    closed_cnt = sum(1 for c in change_rows if c["変動種別"] == "募集終了")
    title = f"【{today_str}】愛媛の空き家パトロール速報｜新着{new_cnt}件・値下げ{down_cnt}件・募集終了{closed_cnt}件"

    # 1. SNSドラフト（新着・値下げ物件を140字以内で出力）
    sns_lines = ["## 1. X・Threads投稿案（1件140字以内）\n"]
    if diff_rows:
        for i, r in enumerate(diff_rows, 1):
            sns_lines.append(f"### {i}. {r['自治体名']} {r['物件ID']}（{r['ステータス']}）")
            sns_lines.append(make_sns_post(r))
            sns_lines.append("")
    else:
        sns_lines.append(f"### 1. 本日の募集終了速報（{closed_cnt}件）")
        sns_lines.append(
            f"【愛媛・空き家動向】\n本日の巡回で{closed_cnt}件の募集終了（成約・取下げ）を確認しました。条件の良い物件は動きが早くなっています。\n全20市町の最新相場マップ👇\nhttps://ehime-akiya-patrol.pages.dev/\n#空き家バンク #愛媛移住"
        )
        sns_lines.append("")

    # 2. note本文（無料公開パート ＋ ペイウォール区切り ＋ メンバーシップ限定パート）
    note_lines = build_free_public_summary(change_rows, today_str) + build_membership_section(change_rows)
    note_body = "\n".join(note_lines)

    # Markdownテーブルも保存用MDには付加
    table_lines = [
        "### 変動履歴サマリーテーブル",
        "| 検知日 | 変動種別 | エリア | 自治体 | 物件ID | 旧価格 | 新価格 | 価格差 | 経過日数 | 公式URL |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in change_rows:
        table_lines.append(
            f"| {c['検知日']} | {c['変動種別']} | {get_region(c['自治体名'])} | {c['自治体名']} | {c['物件ID']} | {c['旧価格'] or '-'} | {c['新価格'] or '-'} | {c['価格差'] or '-'} | {c['経過日数'] or '-'} | {c['詳細URL']} |"
        )

    md_full = f"# {title}\n\n" + "\n".join(sns_lines) + "\n---\n\n## 2. note記事用本文（下書き保存対象）\n\n" + note_body + "\n\n" + "\n".join(table_lines) + "\n"
    md_path.write_text(md_full, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "title": title,
                "body": note_body,
                "date": today_str,
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


def append_change_history(change_rows: list[dict]):
    if not change_rows:
        return
    CHANGE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    need_header = not CHANGE_HISTORY_PATH.exists() or CHANGE_HISTORY_PATH.stat().st_size == 0
    with open(CHANGE_HISTORY_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, CHANGE_HISTORY_FIELDS)
        if need_header:
            w.writeheader()
        w.writerows(change_rows)


def run_patrol(dry_run: bool = False, test_sample_draft: bool = False):
    today_str = date.today().isoformat()
    sources = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    sources_changed = False

    # 既存DB読み込み（初回取得日と最新レコードを両方保持）
    existing_rows = []
    if DB_PATH.exists():
        with open(DB_PATH, encoding="utf-8-sig") as f:
            existing_rows = list(csv.DictReader(f))

    by_url = {}
    by_key = {}
    first_date_by_url = {}
    first_date_by_key = {}
    for r in existing_rows:
        u = r.get("詳細URL", "")
        d = r.get("取得日", "") or today_str
        if u:
            by_url[u] = r
            if u not in first_date_by_url:
                first_date_by_url[u] = d
        if r.get("物件ID"):
            k = (r["自治体名"], r["物件ID"], r["種別"])
            by_key[k] = r
            if k not in first_date_by_key:
                first_date_by_key[k] = d

    # 既に募集終了として記録済みの物件を読み込み（二重計上防止）
    already_closed_urls = set()
    already_closed_keys = set()
    if CHANGE_HISTORY_PATH.exists():
        with open(CHANGE_HISTORY_PATH, encoding="utf-8-sig") as f:
            for ch in csv.DictReader(f):
                u = ch.get("詳細URL", "")
                k = (ch.get("自治体名", ""), ch.get("物件ID", ""))
                if ch.get("変動種別") == "募集終了":
                    if u:
                        already_closed_urls.add(u)
                    if k[1]:
                        already_closed_keys.add(k)
                elif ch.get("変動種別") in ("新着", "値下げ", "価格改定"):
                    already_closed_urls.discard(u)
                    already_closed_keys.discard(k)

    scraped_all = []
    healthy_munis = set()
    for src in sources:
        muni = src["municipality"]
        url = src["list_url"]
        if muni not in SCRAPERS:
            # portal型でもメンテ明け（新居浜 akiya-navi等）の死活だけ確認ログを残す
            if muni == "新居浜市":
                probe = get("https://www.akiya-navi.com/niihama_akiya_bank/", encoding="shift_jis")
                if probe and "メンテナンス" not in probe and "akiya" in probe.lower():
                    log("NOTICE: 新居浜市 akiya-navi.com のメンテナンスが終了した可能性があります！")
            continue

        fn, hint_pat = SCRAPERS[muni]
        log(f"巡回開始: {muni} ({url})")
        items = []
        try:
            items = fn(url)
        except Exception as e:
            log(f"ERROR: {muni} スクレイプ中に例外 ({e})")

        if not items:
            # 自己修復チェック
            healed = self_heal_url(muni, url, hint_pat)
            if healed and healed != url:
                src["list_url"] = healed
                src["last_verified"] = today_str
                src["notes"] = src.get("notes", "") + f" [自己修復:{today_str}]"
                sources_changed = True
                try:
                    items = fn(healed)
                except Exception as e:
                    log(f"ERROR: 自己修復後も取得失敗 {muni} ({e})")

        if items:
            src["last_verified"] = today_str
            sources_changed = True
            healthy_munis.add(muni)
        log(f"巡回完了: {muni} -> {len(items)}件取得")
        scraped_all.extend(items)

    # 取得分内の重複排除
    deduped = []
    seen_now = set()
    current_urls = set()
    current_muni_pids = set()
    for r in scraped_all:
        k = (r["自治体名"], r["物件ID"], r["種別"]) if r.get("物件ID") else r["詳細URL"]
        if k in seen_now:
            continue
        seen_now.add(k)
        deduped.append(r)
        if r.get("詳細URL"):
            current_urls.add(r["詳細URL"])
        if r.get("物件ID"):
            current_muni_pids.add((r["自治体名"], r["物件ID"]))

    # 差分・値下げ・募集終了の抽出
    diff_rows = []
    change_rows = []
    for item in deduped:
        u = item.get("詳細URL", "")
        k = (item["自治体名"], item["物件ID"], item["種別"]) if item.get("物件ID") else None
        prev = by_url.get(u) or (by_key.get(k) if k else None)
        if prev is None:
            row = {x: "" for x in FIELDS}
            row.update(item)
            row["取得日"] = today_str
            row["ステータス"] = "新着"
            diff_rows.append(row)
            change_rows.append({
                "検知日": today_str,
                "変動種別": "新着",
                "自治体名": item.get("自治体名", ""),
                "地区名": item.get("地区名", ""),
                "物件ID": item.get("物件ID", ""),
                "旧価格": "",
                "新価格": item.get("価格", ""),
                "価格差": "",
                "初回取得日": today_str,
                "経過日数": "0日",
                "間取り": item.get("間取り", ""),
                "特記事項": item.get("特記事項", ""),
                "詳細URL": u,
            })
        else:
            old_p = norm_price(prev.get("価格", ""))
            new_p = norm_price(item.get("価格", ""))
            if old_p and new_p and old_p != new_p:
                change_type, diff_str = compute_price_diff(prev.get("価格", ""), item.get("価格", ""))
                first_d = first_date_by_url.get(u) or (first_date_by_key.get(k) if k else None) or prev.get("取得日", today_str)
                elapsed = calc_elapsed_days(first_d, today_str)
                row = {x: "" for x in FIELDS}
                row.update(item)
                row["取得日"] = today_str
                row["ステータス"] = f"{change_type}({prev['価格']}→{item['価格']})"
                diff_rows.append(row)
                change_rows.append({
                    "検知日": today_str,
                    "変動種別": change_type,
                    "自治体名": item.get("自治体名", ""),
                    "地区名": item.get("地区名", ""),
                    "物件ID": item.get("物件ID", ""),
                    "旧価格": prev.get("価格", ""),
                    "新価格": item.get("価格", ""),
                    "価格差": diff_str,
                    "初回取得日": first_d,
                    "経過日数": elapsed,
                    "間取り": item.get("間取り", "") or prev.get("間取り", ""),
                    "特記事項": item.get("特記事項", "") or prev.get("特記事項", ""),
                    "詳細URL": u,
                })

    # 募集終了（前回まで存在していたが今回の正常巡回市町で消えた物件）の抽出
    seen_prev_checked = set()
    for prev in existing_rows:
        muni = prev.get("自治体名", "")
        if muni not in healthy_munis:
            continue
        u = prev.get("詳細URL", "")
        pid = prev.get("物件ID", "")
        k = (muni, pid, prev.get("種別", "")) if pid else u
        if k in seen_prev_checked:
            continue
        seen_prev_checked.add(k)
        # 最新状態のレコードを参照
        latest_prev = by_url.get(u) or (by_key.get((muni, pid, prev.get("種別", ""))) if pid else prev)
        latest_u = latest_prev.get("詳細URL", "")
        latest_pid = latest_prev.get("物件ID", "")

        still_exists = (latest_u and latest_u in current_urls) or (latest_pid and (muni, latest_pid) in current_muni_pids)
        if still_exists:
            continue
        if (latest_u and latest_u in already_closed_urls) or (latest_pid and (muni, latest_pid) in already_closed_keys):
            continue

        first_d = (
            first_date_by_url.get(latest_u)
            or (first_date_by_key.get((muni, latest_pid, latest_prev.get("種別", ""))) if latest_pid else None)
            or latest_prev.get("取得日", today_str)
        )
        elapsed = calc_elapsed_days(first_d, today_str)
        change_rows.append({
            "検知日": today_str,
            "変動種別": "募集終了",
            "自治体名": muni,
            "地区名": latest_prev.get("地区名", ""),
            "物件ID": latest_pid,
            "旧価格": latest_prev.get("価格", ""),
            "新価格": "-",
            "価格差": "-",
            "初回取得日": first_d,
            "経過日数": elapsed,
            "間取り": latest_prev.get("間取り", ""),
            "特記事項": latest_prev.get("特記事項", ""),
            "詳細URL": latest_u,
        })

    log(f"差分結果: 取得総数={len(deduped)} / 新着・価格改定={len(diff_rows)} / 全変動ログ={len(change_rows)}")

    if sources_changed and not dry_run:
        CONFIG_PATH.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")

    if (diff_rows or change_rows) and not dry_run:
        if diff_rows:
            with open(DB_PATH, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, FIELDS)
                w.writerows(diff_rows)
        if change_rows:
            append_change_history(change_rows)
        md_path, json_path = generate_drafts(diff_rows, change_rows, today_str)
        log(f"CSV追記 & ドラフト生成完了: {md_path}")
        print(f"DRAFT_JSON={json_path}")
    elif dry_run:
        for c in change_rows:
            log(f"[DRY-RUN 変動] {c['変動種別']} | {c['自治体名']} {c['物件ID']} | 旧:{c['旧価格']} 新:{c['新価格']} ({c['価格差']}) | 経過:{c['経過日数']} | {c['詳細URL']}")
    elif test_sample_draft:
        # パイプライン動作確認用（DBには書き込まない）
        sample_diffs = []
        sample_changes = []
        for idx, r in enumerate(existing_rows[:3]):
            r_copy = dict(r)
            if idx == 0:
                r_copy["ステータス"] = "新着"
                sample_diffs.append(r_copy)
                sample_changes.append({
                    "検知日": today_str,
                    "変動種別": "新着",
                    "自治体名": r["自治体名"],
                    "地区名": r["地区名"],
                    "物件ID": r["物件ID"],
                    "旧価格": "",
                    "新価格": r["価格"],
                    "価格差": "",
                    "初回取得日": today_str,
                    "経過日数": "0日",
                    "間取り": r["間取り"],
                    "特記事項": r["特記事項"],
                    "詳細URL": r["詳細URL"],
                })
            elif idx == 1:
                r_copy["ステータス"] = "値下げ(350万円→280万円)"
                sample_diffs.append(r_copy)
                sample_changes.append({
                    "検知日": today_str,
                    "変動種別": "値下げ",
                    "自治体名": r["自治体名"],
                    "地区名": r["地区名"],
                    "物件ID": r["物件ID"],
                    "旧価格": "350万円",
                    "新価格": "280万円",
                    "価格差": "-70万円",
                    "初回取得日": r.get("取得日", today_str),
                    "経過日数": "14日",
                    "間取り": r["間取り"],
                    "特記事項": r["特記事項"],
                    "詳細URL": r["詳細URL"],
                })
            else:
                sample_changes.append({
                    "検知日": today_str,
                    "変動種別": "募集終了",
                    "自治体名": r["自治体名"],
                    "地区名": r["地区名"],
                    "物件ID": r["物件ID"],
                    "旧価格": r["価格"],
                    "新価格": "-",
                    "価格差": "-",
                    "初回取得日": r.get("取得日", today_str),
                    "経過日数": "21日",
                    "間取り": r["間取り"],
                    "特記事項": r["特記事項"],
                    "詳細URL": r["詳細URL"],
                })
        md_path, json_path = generate_drafts(sample_diffs, sample_changes, today_str)
        log(f"テスト用ドラフト生成完了: {md_path}")
        print(f"DRAFT_JSON={json_path}")
    else:
        log("新着・価格改定・募集終了がないため、CSV追記・ドラフト生成はスキップしました。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="愛媛県空き家パトロール日次スクリプト")
    ap.add_argument("--dry-run", action="store_true", help="CSV・JSONを更新せず巡回のみ実行")
    ap.add_argument("--test-sample-draft", action="store_true", help="既存上位3件からテスト用ドラフトを生成（DB無変更）")
    args = ap.parse_args()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    run_patrol(dry_run=args.dry_run, test_sample_draft=args.test_sample_draft)
