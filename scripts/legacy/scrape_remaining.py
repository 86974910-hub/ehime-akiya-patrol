import csv
import html
import io
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import certifi
from pypdf import PdfReader

UA = {"User-Agent": "Mozilla/5.0"}
SSL_CTX = ssl.create_default_context(cafile=certifi.where())
TODAY = date.today().isoformat()
CLOSED = re.compile(r"交渉中|協議中|商談中|成約済|成約済み|募集終了")

FIELDS = [
    "取得日", "ステータス", "自治体名", "地区名", "種別", "価格",
    "間取り", "築年数", "土地面積", "建物面積", "特記事項",
    "物件ID", "詳細URL", "掲載元",
]


def get_bytes(u):
    for _ in range(2):
        try:
            req = urllib.request.Request(u, headers=UA)
            return urllib.request.urlopen(req, timeout=25, context=SSL_CTX).read()
        except Exception as e:
            err = e
            time.sleep(0.5)
    print("ERR", u, err, file=sys.stderr)
    return b""


def get(u):
    b = get_bytes(u)
    return b.decode("utf-8", "ignore") if b else ""


def strip_tags(s):
    s = re.sub(r"(?s)<(script|style|noscript|svg).*?</\1>", "", s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def links(s, base):
    out = []
    for m in re.finditer(r'(?s)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', s):
        t = strip_tags(m.group(2))
        out.append((urllib.parse.urljoin(base, html.unescape(m.group(1))), t))
    return out


def parse_kv_pairs(s):
    kv = {}
    for m in re.finditer(r"(?s)<tr[^>]*>(.*?)</tr>", s):
        cells = [strip_tags(c) for c in re.findall(r"(?s)<t[hd][^>]*>.*?</t[hd]>", m.group(1))]
        if len(cells) in (2, 4):
            for i in range(0, len(cells), 2):
                k = cells[i].replace(" ", "").replace("　", "")
                v = cells[i + 1]
                if k and k not in kv:
                    kv[k] = v
    return kv


def parse_dl_pairs(s):
    kv = {}
    for k, v in re.findall(r"(?s)<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", s):
        kk = strip_tags(k).replace(" ", "").replace("　", "")
        vv = strip_tags(v)
        if kk and kk not in kv:
            kv[kk] = vv
    return kv


def rx(pat, t, n=80):
    m = re.search(pat, t)
    return m.group(1).strip()[:n] if m else ""


rows = []


def add(**k):
    r = {x: "" for x in FIELDS}
    r["取得日"] = TODAY
    r["ステータス"] = "新着(初回ベースライン)"
    r.update({kk: (vv or "").strip() for kk, vv in k.items()})
    rows.append(r)


# 1. 西条市 (PDF並列)
print("Scraping 西条市...", file=sys.stderr)
u_sj = "https://www.city.saijo.ehime.jp/soshiki/ijyusuishin/akiya-bank.html"
sj_html = get(u_sj)
sj_tasks = []
seen_sj = set()
for m in re.finditer(r"(?s)<tr[^>]*>(.*?)</tr>", sj_html):
    tds = re.findall(r"(?s)<td[^>]*>(.*?)</td>", m.group(1))
    if len(tds) < 2:
        continue
    status_txt = strip_tags(tds[1])
    if CLOSED.search(status_txt):
        continue
    pdf_rel = rx(r'href="([^"]+\.pdf)"', tds[0], 200)
    if not pdf_rel:
        continue
    pdf_url = urllib.parse.urljoin(u_sj, pdf_rel)
    if pdf_url in seen_sj:
        continue
    seen_sj.add(pdf_url)
    info_txt = strip_tags(tds[0])
    m_id = re.search(r"(?:No\.?|№)\s*(\d+)\s*([^【\s]+)\s*【([^】]+)】", info_txt)
    pid = f"No.{m_id.group(1)}" if m_id else ""
    loc = m_id.group(2).replace("西条市", "").strip() if m_id else ""
    kind = m_id.group(3).strip() if m_id else "売却"
    summary = re.sub(r".*?】\s*", "", info_txt)
    summary = re.sub(r"詳細情報\s*\[PDF.*", "", summary).strip()
    sj_tasks.append((pid, loc, kind, summary, pdf_url))


def fetch_saijo(task):
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
            print("SJ PDF ERR", pdf_url, e, file=sys.stderr)
    return dict(
        自治体名="西条市", 地区名=loc, 種別=kind, 価格=price, 間取り="", 築年数=built,
        土地面積=land, 建物面積=bldg, 特記事項=" / ".join(dict.fromkeys(x for x in notes if x))[:180],
        物件ID=pid, 詳細URL=pdf_url, 掲載元="西条市公式(PDF)"
    )


with ThreadPoolExecutor(max_workers=6) as ex:
    for r in ex.map(fetch_saijo, sj_tasks):
        add(**r)


# 2. 伊予市
print("Scraping 伊予市...", file=sys.stderr)
u_iyo = "https://akisapo-iyo.com/akiya_list"
iyo_html = get(u_iyo)
seen_iyo = set()
for h, tt in links(iyo_html, u_iyo):
    if "/akiya_bank/" not in h or h in seen_iyo:
        continue
    seen_iyo.add(h)
    if CLOSED.search(tt):
        continue
    d = get(h)
    kv = parse_kv_pairs(d)
    if CLOSED.search(d[:800]):
        continue
    kind = "賃貸" if "賃貸" in tt or "貸します" in tt else "売買"
    notes = [kv.get(k, "") for k in ("駐車場", "その他", "都市計画") if kv.get(k)]
    add(
        自治体名="伊予市",
        地区名=kv.get("所在地", "").replace("伊予市", "").strip(),
        種別=kind,
        価格=kv.get("希望価格", ""),
        間取り=kv.get("間取り", ""),
        築年数=kv.get("建築年", ""),
        土地面積=kv.get("土地面積", ""),
        建物面積=kv.get("建物面積", ""),
        特記事項=" / ".join(notes)[:180],
        物件ID=kv.get("登録番号", ""),
        詳細URL=h,
        掲載元="空き家サポート伊予",
    )


# 3. 東温市
print("Scraping 東温市...", file=sys.stderr)
seen_toon = set()
for cat_u, kind in (("https://toon-iju.com/akiya/selling/", "売買"), ("https://toon-iju.com/akiya/lease/", "賃貸")):
    s = get(cat_u)
    for h, _ in links(s, cat_u):
        if not re.search(r"https://toon-iju\.com/akiya/\d+\.html", h) or h in seen_toon:
            continue
        seen_toon.add(h)
        d = get(h)
        if CLOSED.search(strip_tags(d)[:500]):
            continue
        kv = parse_kv_pairs(d)
        add(
            自治体名="東温市",
            地区名=kv.get("所在地", "").replace("東温市", "").strip(),
            種別=kind,
            価格=kv.get("価格", "") or kv.get("賃料", ""),
            間取り=kv.get("間取り", ""),
            築年数=kv.get("築年数", "") or kv.get("建築年", ""),
            土地面積=kv.get("土地面積", ""),
            建物面積=kv.get("建物面積", ""),
            特記事項=kv.get("特記事項（設備条件など）", "")[:180],
            物件ID=rx(r"/akiya/(\d+)\.html", h),
            詳細URL=h,
            掲載元="東温市移住ポータル",
        )


# 4. 上島町 (並列)
print("Scraping 上島町...", file=sys.stderr)
u_km = "https://kamijima-life.jp/residence/archive/"
km_html = get(u_km)
km_urls = list(dict.fromkeys(re.findall(r"https://kamijima-life\.jp/residences/\d+/", km_html)))


def fetch_kamijima(h):
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
    land = rx(r"敷地面積\s*([\d,\.]+\s*㎡)", area_raw)
    bldg = rx(r"延床面積\s*([\d,\.]+\s*㎡)", area_raw)
    return dict(
        自治体名="上島町",
        地区名=kv.get("所在地", ""),
        種別=kind,
        価格=re.sub(r"^(?:売買|賃貸)\s*", "", price_raw).strip(),
        間取り=kv.get("間取り", ""),
        築年数=kv.get("築年数", ""),
        土地面積=land,
        建物面積=bldg,
        特記事項=kv.get("構造", "")[:180],
        物件ID=kv.get("物件番号", "").replace(" ", ""),
        詳細URL=h,
        掲載元="瀬戸内かみじまライフ",
    )


with ThreadPoolExecutor(max_workers=6) as ex:
    for r in ex.map(fetch_kamijima, km_urls):
        if r:
            add(**r)


# 5. 八幡浜市
print("Scraping 八幡浜市...", file=sys.stderr)
seen_yh = set()
yh_tasks = []
for p in range(1, 10):
    u_yh = "https://yawatahama-iju.com/akiya/" + (f"page/{p}/" if p > 1 else "")
    s = get(u_yh)
    if not s or "物件管理番号" not in s:
        break
    found_new = False
    for h, tt in links(s, u_yh):
        if re.search(r"https://yawatahama-iju\.com/akiya/\d+/", h) and h not in seen_yh:
            seen_yh.add(h)
            found_new = True
            if CLOSED.search(tt):
                continue
            yh_tasks.append((h, tt))
    if not found_new:
        break


def fetch_yahata(task):
    h, tt = task
    d = get(h)
    kv = parse_kv_pairs(d)
    pid = rx(r"【物件管理番号】(\d+)", tt)
    kind = "賃貸" if "賃貸" in tt else "売買"
    price = rx(r"([\d,]+\s*万円(?:/月)?|[\d,]+\s*円/月)", tt)
    notes = [kv.get(k, "") for k in ("状態", "駐車場", "設備条件") if kv.get(k)]
    return dict(
        自治体名="八幡浜市",
        地区名=rx(r"(まち暮らし|山暮らし|海暮らし)", tt) or "八幡浜市",
        種別=kind,
        価格=price,
        間取り=rx(r"/\s*([0-9]+[SLDKR]+)", tt) or kv.get("間取り詳細", "")[:30],
        築年数=kv.get("築年月", ""),
        土地面積=kv.get("土地面積", ""),
        建物面積=kv.get("建物面積", ""),
        特記事項=" / ".join(notes)[:180],
        物件ID=f"No.{pid}" if pid else "",
        詳細URL=h,
        掲載元="八幡浜市移住ポータル",
    )


with ThreadPoolExecutor(max_workers=6) as ex:
    for r in ex.map(fetch_yahata, yh_tasks):
        add(**r)


# 6. 大洲市
print("Scraping 大洲市...", file=sys.stderr)
seen_oz = set()
for tp in (11, 12, 21, 31, 32):
    u_oz = f"https://www.city.ozu.ehime.jp/akiyabank/search/search.php?type={tp}"
    s = get(u_oz)
    for h, _ in links(s, u_oz):
        if not re.search(r"/akiyabank/\d+-\d+-\d+\.html", h) or h in seen_oz:
            continue
        seen_oz.add(h)
        d = get(h)
        if CLOSED.search(strip_tags(d)[:500]):
            continue
        kv = parse_kv_pairs(d)
        kind = "賃貸" if tp in (12, 32) else "売買"
        price = kv.get("売買価格") or kv.get("賃貸価格(月額)") or ""
        notes = [kv.get(k, "") for k in ("修繕・解体の要否", "車庫・駐車場", "田畑・菜園") if kv.get(k)]
        add(
            自治体名="大洲市",
            地区名=(kv.get("地域名", "") + " " + kv.get("地区名", "") + " " + kv.get("所在地", "").replace("大洲市", "")).strip(),
            種別=kind,
            価格=price,
            間取り=kv.get("間取り", "") if kv.get("間取り", "") != "間取り図のとおり" else "",
            築年数=kv.get("築年数", ""),
            土地面積=kv.get("敷地(土地)面積", ""),
            建物面積=kv.get("建物面積", ""),
            特記事項=" / ".join(notes)[:180],
            物件ID=kv.get("物件番号", ""),
            詳細URL=h,
            掲載元="大洲市公式",
        )


# 7. 内子町
print("Scraping 内子町...", file=sys.stderr)
u_uc = "https://www.town.uchiko.ehime.jp/teijyu-akiya/search/search.php?search=1&page=1&all=1"
uc_html = get(u_uc)
seen_uc = set()
for h, _ in links(uc_html, u_uc):
    if not re.search(r"/teijyu-akiya/ijyu/akiya\d+\.html", h) or h in seen_uc:
        continue
    seen_uc.add(h)
    d = get(h)
    if CLOSED.search(strip_tags(d)[:500]):
        continue
    kv = parse_kv_pairs(d)
    price = kv.get("売買価格") or kv.get("賃貸価格（月額）") or ""
    kind = "賃貸" if kv.get("賃貸価格（月額）") and not kv.get("売買価格") else "売買"
    notes = [kv.get(k, "") for k in ("修繕・解体の要否", "利用状況") if kv.get(k)]
    add(
        自治体名="内子町",
        地区名=(kv.get("エリア名", "") + " " + kv.get("所在地", "")).strip(),
        種別=kind,
        価格=price,
        間取り="" if kv.get("間取り", "") in ("図面参照", "間取り図のとおり") else kv.get("間取り", ""),
        築年数=kv.get("建築年", ""),
        土地面積=kv.get("敷地（土地）面積", ""),
        建物面積=kv.get("建物面積（延床）", ""),
        特記事項=" / ".join(notes)[:180],
        物件ID=kv.get("物件番号", ""),
        詳細URL=h,
        掲載元="うちこ屋バンク",
    )


# 8. 伊方町
print("Scraping 伊方町...", file=sys.stderr)
u_ik = "https://www.town.ikata.ehime.jp/akiya/search/search.php?hid_flg_search=1&page=1"
ik_html = get(u_ik)
seen_ik = set()
for h, _ in links(ik_html, u_ik):
    if not re.search(r"/akiya/iju-teiju/\d+\.html", h) or h in seen_ik:
        continue
    seen_ik.add(h)
    d = get(h)
    if CLOSED.search(strip_tags(d)[:500]):
        continue
    kv = parse_kv_pairs(d)
    tp = kv.get("種別", "")
    kind = "賃貸" if "貸" in tp else "売買"
    add(
        自治体名="伊方町",
        地区名=(kv.get("地域", "") + " " + kv.get("所在地", "").replace("伊方町", "")).strip(),
        種別=kind,
        価格=kv.get("金額", ""),
        間取り="" if kv.get("間取り", "") == "-" else kv.get("間取り", ""),
        築年数="" if kv.get("築年数", "") == "-" else kv.get("築年数", ""),
        土地面積=kv.get("土地面積", ""),
        建物面積=kv.get("建物面積", ""),
        特記事項=tp,
        物件ID=kv.get("物件番号", ""),
        詳細URL=h,
        掲載元="伊方町公式",
    )


# 9. 愛南町 (PDF)
print("Scraping 愛南町...", file=sys.stderr)
u_ai = "https://www.town.ainan.ehime.jp/kurashi/tetsuduki/sumai/iju/ijujoho.html"
ai_html = get(u_ai)
ai_pdfs = [p for p in dict.fromkeys(re.findall(r'files/[^"]+\.pdf', ai_html)) if "daityou" in p or "_HP" in p]
for p_rel in ai_pdfs:
    pdf_url = urllib.parse.urljoin(u_ai, p_rel)
    pb = get_bytes(pdf_url)
    if not pb:
        continue
    try:
        pt = PdfReader(io.BytesIO(pb)).pages[0].extract_text() or ""
        pid = rx(r"登\s*録\s*番\s*号\s*(\S+)", pt, 20)
        loc = rx(r"所\s*在\s*地\s*([^\n]+)", pt, 40).replace("愛南町", "").strip()
        price = rx(r"([\d,]+\s*万(?:円)?(?:/月)?|[\d,]+\s*円/月)", pt, 30)
        built = rx(r"(築\s*\d+\s*年|昭和\s*\d+\s*年|平成\s*\d+\s*年|令和\s*\d+\s*年)", pt, 20)
        land = rx(r"土地\s*([\d,\.]+)", pt, 20)
        if land:
            land += "㎡"
        kind = "賃貸" if "円/月" in price else "売買"
        add(
            自治体名="愛南町",
            地区名=loc,
            種別=kind,
            価格=price,
            間取り="",
            築年数=built,
            土地面積=land,
            建物面積="",
            特記事項=rx(r"(津波[^\n]+|駐車場[^\n]+)", pt, 80),
            物件ID=pid,
            詳細URL=pdf_url,
            掲載元="愛南町公式(PDF)",
        )
    except Exception as e:
        print("AINAN PDF ERR", pdf_url, e, file=sys.stderr)


# 10. 松山市(離島)
print("Scraping 松山市(離島)...", file=sys.stderr)
u_rt = "https://ritou-akiya.com/house"
rt_html = get(u_rt)
seen_rt = set()
for h, _ in links(rt_html, u_rt):
    if not re.search(r"https://ritou-akiya\.com/house/[^/\"?#]+$", h) or h in seen_rt:
        continue
    seen_rt.add(h)
    d = get(h)
    if CLOSED.search(strip_tags(d)[:400]):
        continue
    kv = parse_kv_pairs(d)
    kind = "賃貸" if "貸" in kv.get("利用種別", "") else "売買"
    add(
        自治体名="松山市",
        地区名=kv.get("島名・地区", "") or kv.get("所在地", ""),
        種別=kind,
        価格=kv.get("価格", ""),
        間取り=kv.get("間取り", ""),
        築年数=kv.get("築年", ""),
        土地面積="",
        建物面積=kv.get("延べ床", ""),
        特記事項=kv.get("間取り詳細", "")[:180],
        物件ID=h.rsplit("/", 1)[-1],
        詳細URL=h,
        掲載元="里島空き家バンク(松山市離島)",
    )


# 既存CSVの6自治体とマージ
new_munis = {r["自治体名"] for r in rows}
existing = list(csv.DictReader(open("ehime_akiya_db.csv", encoding="utf-8-sig")))
merged = [r for r in existing if r["自治体名"] not in new_munis] + rows

with open("ehime_akiya_db.csv", "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, FIELDS)
    w.writeheader()
    w.writerows(merged)

print("NEWLY ADDED:", Counter(r["自治体名"] for r in rows))
print("TOTAL IN DB:", Counter(r["自治体名"] for r in merged), "ALL:", len(merged))
