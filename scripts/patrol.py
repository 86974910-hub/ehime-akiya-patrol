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


def make_sns_post(r: dict) -> str:
    muni = r["自治体名"]
    area = r["地区名"].split()[0] if r["地区名"] else ""
    loc_tag = f"{muni}({area})" if area and area != muni else muni
    price = r["価格"] or "要相談"
    layout = f"・{r['間取り']}" if r["間取り"] else ""
    prefix = "【愛媛・空き家値下げ】" if "価格改定" in r["ステータス"] else "【愛媛・空き家速報】"
    tags = "#空き家バンク #古民家暮らし #地方移住 #愛媛移住"
    feat = r["特記事項"].replace("\n", " ").strip()
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


def generate_drafts(diff_rows: list, today_str: str):
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    ymd = today_str.replace("-", "")
    md_path = DRAFTS_DIR / f"daily_{ymd}.md"
    json_path = DRAFTS_DIR / f"daily_{ymd}_note.json"

    new_cnt = sum(1 for r in diff_rows if r["ステータス"] == "新着")
    chg_cnt = sum(1 for r in diff_rows if r["ステータス"].startswith("価格改定"))
    title = f"【{today_str}】愛媛の空き家パトロール速報｜新着{new_cnt}件・価格改定{chg_cnt}件"

    # 1. SNSドラフト
    sns_lines = ["## 1. X・Threads投稿案（1件140字以内）\n"]
    for i, r in enumerate(diff_rows, 1):
        sns_lines.append(f"### {i}. {r['自治体名']} {r['物件ID']}（{r['ステータス']}）")
        sns_lines.append(make_sns_post(r))
        sns_lines.append("")

    # 2. note本文（そのままnoteエディタへ投入する部分）
    note_lines = [
        f"こんにちは、愛媛県移住・古民家メディアです。",
        f"{today_str}の愛媛県内空き家バンク巡回で確認できた「新着物件（{new_cnt}件）」および「価格改定物件（{chg_cnt}件）」をエリア別にまとめました。",
        "※画像は転載せず、公式ページの一次情報のみを整理しています。最新状況や内見申込は各公式URLをご確認ください。",
        "",
    ]
    for area_name in ("東予", "中予", "南予"):
        subset = [r for r in diff_rows if get_region(r["自治体名"]) == area_name]
        if not subset:
            continue
        note_lines.append(f"■ {area_name}エリア（{len(subset)}件）")
        note_lines.append("")
        for r in subset:
            spec_parts = [x for x in (
                f"間取り: {r['間取り']}" if r["間取り"] else "",
                f"築年: {r['築年数']}" if r["築年数"] else "",
                f"土地: {r['土地面積']}" if r["土地面積"] else "",
                f"建物: {r['建物面積']}" if r["建物面積"] else "",
            ) if x]
            note_lines.extend([
                f"・【{r['ステータス']}】{r['自治体名']} {r['物件ID']}（{r['地区名'] or '地区要確認'}）",
                f"  種別／価格：{r['種別']} ／ {r['価格'] or '要相談'}",
                f"  スペック：{' / '.join(spec_parts) if spec_parts else '公式ページ参照'}",
                f"  特徴：{r['特記事項'] or '特記なし'}",
                f"  公式URL：{r['詳細URL']}",
                "  【地元民リアルメモ追記欄】：（周辺環境・生活利便性・注意点などを追記）",
                "",
            ])

    note_lines.extend([
        "---",
        "※本記事は自動パトロールで抽出した速報をもとに作成しています。商談中・成約済に切り替わっている場合もあるため、最新情報はリンク先の自治体公式窓口にてご確認ください。",
        "",
        "#空き家バンク #古民家暮らし #地方移住 #愛媛移住",
    ])
    note_body = "\n".join(note_lines)

    # Markdownテーブルも保存用MDには付加
    table_lines = [
        "### 週間まとめ表追記用テーブル",
        "| エリア | 自治体 | ステータス | 価格 | スペック | 公式URL | 地元民リアルメモ |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in diff_rows:
        spec = " / ".join(x for x in (r["間取り"], r["築年数"], r["土地面積"], r["建物面積"]) if x) or "-"
        table_lines.append(
            f"| {get_region(r['自治体名'])} | {r['自治体名']} | {r['ステータス']} | {r['価格'] or '要相談'} | {spec} | {r['詳細URL']} | 【地元民リアルメモ追記欄】 |"
        )

    md_full = f"# {title}\n\n" + "\n".join(sns_lines) + "\n---\n\n## 2. note記事用本文（下書き保存対象）\n\n" + note_body + "\n\n" + "\n".join(table_lines) + "\n"
    md_path.write_text(md_full, encoding="utf-8")
    json_path.write_text(
        json.dumps({"title": title, "body": note_body, "date": today_str, "new_count": new_cnt, "price_changed_count": chg_cnt, "md_path": str(md_path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return md_path, json_path


def run_patrol(dry_run: bool = False, test_sample_draft: bool = False):
    today_str = date.today().isoformat()
    sources = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    sources_changed = False

    # 既存DB読み込み
    existing_rows = []
    if DB_PATH.exists():
        with open(DB_PATH, encoding="utf-8-sig") as f:
            existing_rows = list(csv.DictReader(f))

    by_url = {}
    by_key = {}
    for r in existing_rows:
        if r.get("詳細URL"):
            by_url[r["詳細URL"]] = r
        if r.get("物件ID"):
            by_key[(r["自治体名"], r["物件ID"], r["種別"])] = r

    scraped_all = []
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
        log(f"巡回完了: {muni} -> {len(items)}件取得")
        scraped_all.extend(items)

    # 取得分内の重複排除
    deduped = []
    seen_now = set()
    for r in scraped_all:
        k = (r["自治体名"], r["物件ID"], r["種別"]) if r.get("物件ID") else r["詳細URL"]
        if k in seen_now:
            continue
        seen_now.add(k)
        deduped.append(r)

    # 差分・価格改定抽出
    diff_rows = []
    for item in deduped:
        prev = by_url.get(item["詳細URL"]) or (by_key.get((item["自治体名"], item["物件ID"], item["種別"])) if item.get("物件ID") else None)
        if prev is None:
            row = {x: "" for x in FIELDS}
            row.update(item)
            row["取得日"] = today_str
            row["ステータス"] = "新着"
            diff_rows.append(row)
        else:
            old_p = norm_price(prev.get("価格", ""))
            new_p = norm_price(item.get("価格", ""))
            if old_p and new_p and old_p != new_p:
                row = {x: "" for x in FIELDS}
                row.update(item)
                row["取得日"] = today_str
                row["ステータス"] = f"価格改定({prev['価格']}→{item['価格']})"
                diff_rows.append(row)

    log(f"差分結果: 取得総数={len(deduped)} / 新着・価格改定={len(diff_rows)}")

    if sources_changed and not dry_run:
        CONFIG_PATH.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")

    if diff_rows and not dry_run:
        with open(DB_PATH, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, FIELDS)
            w.writerows(diff_rows)
        md_path, json_path = generate_drafts(diff_rows, today_str)
        log(f"CSV追記 & ドラフト生成完了: {md_path}")
        print(f"DRAFT_JSON={json_path}")
    elif dry_run:
        for r in diff_rows:
            log(f"[DRY-RUN 差分] {r['ステータス']} | {r['自治体名']} {r['物件ID']} | {r['価格']} | {r['詳細URL']}")
    elif test_sample_draft:
        # パイプライン動作確認用（DBには書き込まない）
        sample = existing_rows[:3]
        for s in sample:
            s = dict(s)
            s["ステータス"] = "新着(動作確認サンプル)"
        md_path, json_path = generate_drafts([dict(r, ステータス="新着(テスト)") for r in existing_rows[:3]], today_str)
        log(f"テスト用ドラフト生成完了: {md_path}")
        print(f"DRAFT_JSON={json_path}")
    else:
        log("新着・価格改定がないため、CSV追記・ドラフト生成はスキップしました。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="愛媛県空き家パトロール日次スクリプト")
    ap.add_argument("--dry-run", action="store_true", help="CSV・JSONを更新せず巡回のみ実行")
    ap.add_argument("--test-sample-draft", action="store_true", help="既存上位3件からテスト用ドラフトを生成（DB無変更）")
    args = ap.parse_args()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    run_patrol(dry_run=args.dry_run, test_sample_draft=args.test_sample_draft)
