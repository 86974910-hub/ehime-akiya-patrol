import csv
import html
import io
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date
import certifi
from pypdf import PdfReader

UA = {"User-Agent": "Mozilla/5.0"}
SSL_CTX = ssl.create_default_context(cafile=certifi.where())
TODAY = date.today().isoformat()
CLOSED = re.compile(r"交渉中|協議中|商談中|成約済|成約済み")

FIELDS = [
    "取得日",
    "ステータス",
    "自治体名",
    "地区名",
    "種別",
    "価格",
    "間取り",
    "築年数",
    "土地面積",
    "建物面積",
    "特記事項",
    "物件ID",
    "詳細URL",
    "掲載元",
]


def get_bytes(u):
    for _ in range(2):
        try:
            req = urllib.request.Request(u, headers=UA)
            return urllib.request.urlopen(req, timeout=25, context=SSL_CTX).read()
        except Exception as e:
            err = e
            time.sleep(1)
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


def clean_text(s):
    s = re.sub(r"(?s)<(script|style|noscript|svg).*?</\1>", "", s)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = html.unescape(s).replace("\u200b", "").replace("\xa0", " ")
    return "\n".join(l.strip() for l in s.split("\n") if l.strip())


def links(s, base):
    out = []
    for m in re.finditer(r'(?s)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', s):
        t = strip_tags(m.group(2))
        out.append((urllib.parse.urljoin(base, html.unescape(m.group(1))), t))
    return out


def parse_th_td(s):
    kv = {}
    for m in re.finditer(r"(?s)<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", s):
        k = strip_tags(m.group(1)).replace(" ", "").replace("　", "")
        v = strip_tags(m.group(2))
        if k and k not in kv:
            kv[k] = v
    return kv


def parse_all_rows(s):
    rows = []
    for m in re.finditer(r"(?s)<tr[^>]*>(.*?)</tr>", s):
        cells = [
            strip_tags(c)
            for c in re.findall(r"(?s)<t[hd][^>]*>.*?</t[hd]>", m.group(1))
        ]
        if cells:
            rows.append(cells)
    return rows


def parse_kv_pairs(s):
    kv = {}
    for cells in parse_all_rows(s):
        if len(cells) in (2, 4):
            for i in range(0, len(cells), 2):
                k = cells[i].replace(" ", "").replace("　", "")
                v = cells[i + 1]
                if k and k not in kv:
                    kv[k] = v
    return kv


def rx(pat, t, n=60):
    m = re.search(pat, t)
    return m.group(1).strip()[:n] if m else ""


rows = []


def add(**k):
    r = {x: "" for x in FIELDS}
    r["取得日"] = TODAY
    r["ステータス"] = "新着(初回ベースライン)"
    r.update({kk: (vv or "").strip() for kk, vv in k.items()})
    rows.append(r)


# 1. 今治市
print("Scraping 今治市...", file=sys.stderr)
seen = set()
for cat, kind in (("baibai-bukken", "売買"), ("chintai-bukken", "賃貸")):
    p = 1
    while p <= 15:
        u = f"https://iju-imabari.jp/category/akiya-bank/{cat}/" + (f"page/{p}/" if p > 1 else "")
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
            t = clean_text(d)
            if CLOSED.search(t[:400]):
                continue
            kv = parse_th_td(d)
            pid = rx(r"【物件No\.([^】]+)】", t)
            area = rx(r"エリア\n([^\n]+)", t)
            loc = kv.get("所在地", "").replace("愛媛県今治市", "").strip()
            price = kv.get("価格") or kv.get("賃料") or ""
            notes = []
            if kv.get("備考"):
                notes.append(kv["備考"])
            if kv.get("駐車場"):
                notes.append(f"駐車場:{kv['駐車場']}")
            if kv.get("こだわりポイント"):
                notes.append(kv["こだわりポイント"])
            add(
                自治体名="今治市",
                地区名=(area + " " + loc).strip(),
                種別=kind,
                価格=price,
                間取り=kv.get("間取り", ""),
                築年数=kv.get("築年月", ""),
                土地面積=kv.get("土地面積", ""),
                建物面積=kv.get("建物面積", ""),
                特記事項=" / ".join(notes)[:180],
                物件ID=pid,
                詳細URL=h,
                掲載元="いまばり空き家バンク",
            )
            time.sleep(0.2)
        p += 1


# 2. 久万高原町
print("Scraping 久万高原町...", file=sys.stderr)
u = "https://www.kumakogen.jp/site/iju/list98-409.html"
s = get(u)
seen_kuma = set()
for h, lt in links(s, u):
    m = re.match(r"(【[^】]*】)?\s*(No\.?\s*[\d\-]+)\s*(久万高原町\S*)\s*(.*)", lt)
    if not (m and "/site/iju/" in h and h not in seen_kuma):
        continue
    seen_kuma.add(h)
    if CLOSED.search(lt):
        continue
    d = get(h)
    if not d:
        continue
    t = clean_text(d)
    if CLOSED.search(t[:400]):
        continue
    kv = parse_kv_pairs(d)
    loc = kv.get("所在地", m.group(3)).replace("久万高原町", "").strip()
    kind_raw = kv.get("売却/賃貸", "")
    kind = "賃貸" if "賃貸" in kind_raw or "賃貸" in lt else "売買"
    notes = []
    if "建物なし" in lt:
        notes.append("建物なし(土地)")
    if m.group(1):
        notes.append("民間事業者所有")
    if kv.get("修繕の要否"):
        notes.append(f"修繕:{kv['修繕の要否']}")
    if kv.get("駐車場"):
        notes.append(f"駐車場:{kv['駐車場']}")
    if kv.get("農地・山林等"):
        ag = kv["農地・山林等"]
        if any(x in ag for x in ("畑：", "田：", "山林：")) and not all(
            x in ag for x in ("畑：なし", "田：なし", "山林：なし")
        ):
            notes.append(ag[:60])
    if kv.get("所有者様からのコメント"):
        notes.append(kv["所有者様からのコメント"][:80])
    add(
        自治体名="久万高原町",
        地区名=loc,
        種別=kind,
        価格=kv.get("希望価格", ""),
        間取り="",
        築年数=kv.get("建築年", ""),
        土地面積=kv.get("敷地面積", ""),
        建物面積="",
        特記事項=" / ".join(notes)[:180],
        物件ID=m.group(2).replace(" ", ""),
        詳細URL=h,
        掲載元="久万高原町公式",
    )
    time.sleep(0.2)


# 3. 宇和島市
print("Scraping 宇和島市...", file=sys.stderr)
u = "https://www.city.uwajima.ehime.jp/site/ijyu/akiyabank.html"
s = get(u)
for h in dict.fromkeys(h for h, _ in links(s, u) if re.search(r"/site/ijyu/akiya(bank)?\d+\.html", h)):
    d = get(h)
    if not d:
        continue
    t = clean_text(d)
    if CLOSED.search(t[:400]):
        continue
    trows = parse_all_rows(d)
    kv = parse_kv_pairs(d)
    # 表1: 所在地 | 価格 | 建築年 | 構造 | 間取り
    loc = price = built = layout = ""
    for i, r in enumerate(trows):
        if len(r) >= 4 and "所在地" in r[0] and i + 1 < len(trows):
            vals = trows[i + 1]
            if len(vals) >= 5:
                loc, price, built, _, layout = vals[:5]
            break
    pid_m = re.search(r"空き家バンク物件紹介（(\d+)\.", t) or re.search(r"akiya(?:bank)?(\d+)\.html", h)
    pid = f"No.{pid_m.group(1)}" if pid_m else ""
    kind = "賃貸" if ("賃貸" in price or ("賃貸" in kv.get("区分", "") and "売買" not in kv.get("区分", ""))) else "売買"
    land = kv.get("土地面積", "")
    bldg = kv.get("建物面積（延べ床）", "") or kv.get("建物面積", "")
    # Clean multiline-collapsed spaces in 建物面積
    bldg = re.sub(r"（1階.*", "", bldg).strip()
    notes = []
    if kv.get("駐車場"):
        notes.append(f"駐車場:{kv['駐車場']}")
    if kv.get("補修要否"):
        notes.append(f"補修:{kv['補修要否']}")
    if kv.get("畑") and kv.get("畑") != "なし":
        notes.append(f"畑:{kv['畑']}")
    if kv.get("庭・畑") and kv.get("庭・畑") != "なし":
        notes.append(f"庭・畑:{kv['庭・畑']}")
    if kv.get("特記事項"):
        notes.append(kv["特記事項"])
    add(
        自治体名="宇和島市",
        地区名=loc,
        種別=kind,
        価格=re.sub(r"^(?:売買|賃貸)\s*", "", price).strip(),
        間取り=layout or kv.get("間取り", ""),
        築年数=built,
        土地面積=land,
        建物面積=bldg,
        特記事項=" / ".join(notes)[:180],
        物件ID=pid,
        詳細URL=h,
        掲載元="宇和島市公式",
    )
    time.sleep(0.2)


# 4. 四国中央市 (PDFパース)
print("Scraping 四国中央市...", file=sys.stderr)
seen_sc = set()
for p in (1, 2, 3):
    u = "https://shikokuchuoiju.jp/akiyabank/" + (f"page/{p}/" if p > 1 else "")
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
        title = strip_tags(h3.group(1))
        m_id = re.search(r"(?:No\.?|№)\s*(\d+)\s*([^【]+)【([^】]+)】", title)
        if not m_id:
            continue
        pid = "No." + m_id.group(1)
        if pid in seen_sc:
            continue
        seen_sc.add(pid)
        loc = m_id.group(2).strip()
        kind = m_id.group(3).strip()
        summary = strip_tags(rx(r'<p[^>]*class="u-mb15"[^>]*>(.*?)</p>', it, 200))
        pdf_rel = rx(r'href="([^"]+\.pdf)"', it, 300)
        pdf_url = urllib.parse.urljoin(u, pdf_rel) if pdf_rel else u

        price = land = bldg = built = ""
        pdf_notes = [summary] if summary else []
        if pdf_rel:
            pb = get_bytes(pdf_url)
            if pb:
                try:
                    reader = PdfReader(io.BytesIO(pb))
                    pt = reader.pages[0].extract_text() or ""
                    # 希望価格
                    pm = re.search(r"希望価格\s*(.+)", pt)
                    if pm:
                        price = re.sub(r"\s+", " ", pm.group(1)).strip()
                    # 土地面積・建物延床面積
                    lm = re.search(r"土地\s*([\d,\.]+\s*㎡)", pt)
                    if lm:
                        land = lm.group(1).replace(" ", "")
                    bm = re.search(r"延床面積\s*([\d,\.]+\s*㎡)", pt)
                    if bm:
                        bldg = bm.group(1).replace(" ", "")
                    ym = re.search(r"建築年\s*([^\n]+)", pt)
                    if ym:
                        yv = ym.group(1).strip()
                        if yv and yv != "年":
                            built = yv
                    # 駐車場
                    pkm = re.search(r"駐車場\s*([^\n□■]+)", pt)
                    if pkm:
                        pk = pkm.group(1).strip()
                        if pk:
                            pdf_notes.append(f"駐車場:{pk}")
                    # 補修
                    rm = re.search(r"■([^\s□■]*補修[^\s□■]*)", pt)
                    if rm:
                        pdf_notes.append(rm.group(1))
                    # 特記事項
                    tm = re.search(r"特記事項\s*([\s\S]{1,120})", pt)
                    if tm:
                        tn = re.sub(r"\s+", " ", tm.group(1)).strip()
                        if tn:
                            pdf_notes.append(tn)
                except Exception as e:
                    print("PDF ERR", pdf_url, e, file=sys.stderr)

        add(
            自治体名="四国中央市",
            地区名=loc,
            種別=kind,
            価格=price,
            間取り="",
            築年数=built,
            土地面積=land,
            建物面積=bldg,
            特記事項=" / ".join(dict.fromkeys(x for x in pdf_notes if x))[:180],
            物件ID=pid,
            詳細URL=pdf_url,
            掲載元="四国のまんなか生活(PDF)",
        )
        time.sleep(0.2)


# 5. 松野町
print("Scraping 松野町...", file=sys.stderr)
u = "https://www.town.matsuno.ehime.jp/site/akiya/"
s = get(u)
seen_ma = set()
for h, tt in links(s, u):
    if re.search(r"/site/akiya/\d+\.html", h) and "空き家バンク" in tt and ("No" in tt or "Ｎｏ" in tt):
        if h in seen_ma or CLOSED.search(tt):
            continue
        seen_ma.add(h)
        d = get(h)
        if not d:
            continue
        t = clean_text(d)
        if CLOSED.search(t[:400]):
            continue
        kv = parse_kv_pairs(d)
        pid_m = re.search(r"(?:No|Ｎｏ)[,\s\.]*(\d+)", tt)
        pid = f"No.{pid_m.group(1)}" if pid_m else ""
        loc = kv.get("所在地", "").replace("松野町大字", "").replace("松野町", "").strip()
        if not loc:
            loc = rx(r"(?:No|Ｎｏ)[,\s]*\d+\s*([^\s（(]+)", tt, 25)
        kind_raw = kv.get("賃貸・売却", "")
        kind = "賃貸" if "賃貸" in kind_raw and "売買" not in kind_raw and "売却" not in kind_raw else "売買"
        if "賃貸" in kind_raw and ("売買" in kind_raw or "売却" in kind_raw):
            kind = "売買・賃貸"
        # 建築面積・宅地面積
        struct_all = kv.get("構造", "")
        bldg = rx(r"建築面積\s*([\d,\.]+\s*(?:平方メートル|㎡))", struct_all)
        land = rx(r"宅地面積\s*([\d,\.]+\s*(?:平方メートル|㎡))", struct_all)
        notes = []
        if kv.get("付帯設備"):
            notes.append(kv["付帯設備"])
        if kv.get("特記事項"):
            notes.append(kv["特記事項"])
        add(
            自治体名="松野町",
            地区名=loc,
            種別=kind,
            価格=kv.get("希望価格", ""),
            間取り=kv.get("間取り", "") if kv.get("間取り", "") != "間取り図のとおり" else "",
            築年数=kv.get("建築時期", ""),
            土地面積=land,
            建物面積=bldg,
            特記事項=" / ".join(notes)[:180] or tt[:60],
            物件ID=pid,
            詳細URL=h,
            掲載元="松野町公式",
        )
        time.sleep(0.2)


# 6. 鬼北町
print("Scraping 鬼北町...", file=sys.stderr)
u = "https://kihoku.umiyama-kurashi.jp/bukken_archive"
s = get(u)
seen_ki = set()
for h, tt in links(s, u):
    if "/t_bukken/" in h and "価格" in tt and h not in seen_ki:
        seen_ki.add(h)
        d = get(h)
        kv = parse_kv_pairs(d)
        other = kv.get("その他", "")
        land = rx(r"宅地\s*([\d,\.]+\s*平米)", other)
        loc_head = rx(r"^(\S+)〔", tt)
        area_sub = kv.get("地区", rx(r"地区\s+(\S+地区)", tt))
        notes = []
        if kv.get("駐車場"):
            notes.append(f"駐車場:{kv['駐車場']}")
        if other:
            notes.append(other[:140])
        add(
            自治体名="鬼北町",
            地区名=f"{loc_head}({area_sub})" if loc_head else area_sub,
            種別="売買",
            価格=kv.get("価格", rx(r"価格\s+(\S+)", tt)),
            間取り=kv.get("間取り", rx(r"間取り\s+(\S+)", tt)),
            築年数=kv.get("築年数", rx(r"築年数\s+(\S+)", tt)),
            土地面積=land,
            建物面積=kv.get("面積", rx(r"面積\s+(\S+)", tt)),
            特記事項=" / ".join(notes)[:180],
            物件ID=rx(r"〔(\d+)〕", tt),
            詳細URL=h,
            掲載元="鬼北町空き家情報",
        )
        time.sleep(0.2)


with open("ehime_akiya_db.csv", "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, FIELDS)
    w.writeheader()
    w.writerows(rows)

print("DONE:", Counter(r["自治体名"] for r in rows), "TOTAL:", len(rows))
