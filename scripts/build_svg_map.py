#!/usr/bin/env python3
"""
国土交通省「国土数値情報（行政区域データ N03）」の公式GISポリゴンデータから、
愛媛県全20市町＋周辺県（広島・山口・香川・徳島・高知・大分）の海岸線・境界線を
高精度に投影したインタラクティブSVG地図を生成し、`web/index.html` に組み込むスクリプト。
"""

import json
import math
import re
import ssl
import urllib.request
from pathlib import Path
import certifi

BASE_DIR = Path(__file__).resolve().parent.parent
GEO_CACHE_DIR = BASE_DIR / "data" / "geojson_cache"
INDEX_HTML_PATH = BASE_DIR / "web" / "index.html"

SSL_CTX = ssl.create_default_context(cafile=certifi.where())
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# 投影設定（viewBox="0 0 860 720"）
W, H = 860, 720
LON_MIN, LON_MAX = 131.94, 133.76
LAT_MIN, LAT_MAX = 32.85, 34.36
MID_LAT = 33.60
COS_LAT = math.cos(math.radians(MID_LAT))

GEO_W = (LON_MAX - LON_MIN) * COS_LAT
GEO_H = LAT_MAX - LAT_MIN
SCALE = min((W - 36) / GEO_W, (H - 36) / GEO_H)
PAD_X = (W - GEO_W * SCALE) / 2.0
PAD_Y = (H - GEO_H * SCALE) / 2.0


def fetch_geojson(pref_code: str) -> dict:
    GEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_fp = GEO_CACHE_DIR / f"N03-21_{pref_code}_210101.json"
    if cache_fp.exists():
        return json.loads(cache_fp.read_text(encoding="utf-8"))
    url = f"https://raw.githubusercontent.com/smartnews-smri/japan-topography/main/data/municipality/geojson/s0010/N03-21_{pref_code}_210101.json"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25, context=SSL_CTX) as resp:
        raw = resp.read()
    cache_fp.write_bytes(raw)
    return json.loads(raw.decode("utf-8"))


def proj(lon: float, lat: float) -> tuple[float, float]:
    x = PAD_X + (lon - LON_MIN) * COS_LAT * SCALE
    y = PAD_Y + (LAT_MAX - lat) * SCALE
    return round(x, 1), round(y, 1)


def ring_to_path(ring: list) -> str:
    pts = []
    prev = None
    for lon, lat in ring:
        pt = proj(lon, lat)
        if pt != prev:
            pts.append(pt)
            prev = pt
    if len(pts) < 3:
        return ""
    coords = "L".join(f"{x},{y}" for x, y in pts)
    return f"M{coords}Z"


def geom_to_path(geom: dict, clip_bbox: bool = False) -> str:
    gtype = geom["type"]
    polys = geom["coordinates"] if gtype == "MultiPolygon" else [geom["coordinates"]]
    subpaths = []
    for poly in polys:
        outer = poly[0]
        if clip_bbox:
            # ビューポートから完全に外れているポリゴンは除外
            lons = [p[0] for p in outer]
            lats = [p[1] for p in outer]
            if max(lons) < LON_MIN - 0.15 or min(lons) > LON_MAX + 0.15:
                continue
            if max(lats) < LAT_MIN - 0.15 or min(lats) > LAT_MAX + 0.15:
                continue
        for ring in poly:
            sp = ring_to_path(ring)
            if sp:
                subpaths.append(sp)
    return " ".join(subpaths)


def poly_area_centroid(ring: list) -> tuple[float, tuple[float, float]]:
    pts = [proj(lon, lat) for lon, lat in ring]
    if len(pts) < 3:
        return 0.0, (0.0, 0.0)
    a = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    a *= 0.5
    if abs(a) < 1e-5:
        return 0.0, (round(sum(p[0] for p in pts) / len(pts), 1), round(sum(p[1] for p in pts) / len(pts), 1))
    cx /= 6.0 * a
    cy /= 6.0 * a
    return abs(a), (round(cx, 1), round(cy, 1))


# 各市町の重心と視覚的に最も読みやすいラベル配置（面積が小さい市町は引き出し線を使用）
LABEL_ADJUSTMENTS = {
    "上島町": {"lx": 592.0, "ly": 52.0, "ax": 542.0, "ay": 73.0},
    "今治市": {"dx": 0.0, "dy": -6.0},
    "西条市": {"dx": 0.0, "dy": 4.0},
    "新居浜市": {"dx": 0.0, "dy": -10.0},
    "四国中央市": {"dx": 6.0, "dy": -2.0},
    "松山市": {"dx": 4.0, "dy": -10.0},
    "東温市": {"dx": 8.0, "dy": -2.0},
    "久万高原町": {"dx": 6.0, "dy": 6.0},
    "松前町": {"lx": 300.0, "ly": 266.0, "ax": 386.0, "ay": 278.0},
    "砥部町": {"lx": 438.0, "ly": 308.0},
    "伊予市": {"lx": 354.0, "ly": 324.0},
    "内子町": {"dx": 6.0, "dy": 0.0},
    "大洲市": {"dx": -6.0, "dy": -2.0},
    "八幡浜市": {"dx": -14.0, "dy": -6.0},
    "伊方町": {"lx": 165.0, "ly": 392.0, "ax": 199.9, "ay": 435.0},
    "西予市": {"dx": -8.0, "dy": 4.0},
    "宇和島市": {"dx": -10.0, "dy": 4.0},
    "鬼北町": {"dx": 10.0, "dy": -6.0},
    "松野町": {"lx": 442.0, "ly": 555.0, "ax": 382.1, "ay": 535.4},
    "愛南町": {"dx": 0.0, "dy": 8.0},
}


def generate_svg() -> str:
    ehime_js = fetch_geojson("38")

    # 周辺県（34:広島, 35:山口, 36:徳島, 37:香川, 39:高知, 44:大分）の海岸線・陸地
    neighbor_codes = ["34", "35", "36", "37", "39", "44"]
    neighbor_paths = []
    for code in neighbor_codes:
        n_js = fetch_geojson(code)
        for feat in n_js.get("features", []):
            d_str = geom_to_path(feat["geometry"], clip_bbox=True)
            if d_str:
                neighbor_paths.append(d_str)

    # 愛媛県20市町ポリゴン
    muni_groups = []
    ehime_all_subpaths = []
    for feat in ehime_js["features"]:
        name = feat["properties"]["N03_004"]
        geom = feat["geometry"]
        d_str = geom_to_path(geom, clip_bbox=False)
        ehime_all_subpaths.append(d_str)

        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        best_a = -1.0
        best_c = (0.0, 0.0)
        for poly in polys:
            a, c = poly_area_centroid(poly[0])
            if a > best_a:
                best_a = a
                best_c = c

        cx, cy = best_c
        adj = LABEL_ADJUSTMENTS.get(name, {})
        if "lx" in adj and "ly" in adj:
            lx, ly = adj["lx"], adj["ly"]
        else:
            lx = round(cx + adj.get("dx", 0.0), 1)
            ly = round(cy + adj.get("dy", 0.0), 1)

        anchor_attrs = ""
        if "ax" in adj and "ay" in adj:
            anchor_attrs = f' data-ax="{adj["ax"]}" data-ay="{adj["ay"]}"'

        muni_groups.append(
            f'                <g class="muni-group" data-muni="{name}" data-cx="{lx}" data-cy="{ly}"{anchor_attrs}>\n'
            f'                  <path class="muni-shape" d="{d_str}" />\n'
            f'                </g>'
        )

    # 経度・緯度グリッド線（33°00′N, 33°30′N, 34°00′N / 132°30′E, 133°00′E, 133°30′E）
    graticule_lines = []
    for lat_val, label in ((33.0, "33°00′N"), (33.5, "33°30′N"), (34.0, "34°00′N")):
        _, gy = proj(LON_MIN, lat_val)
        graticule_lines.append(
            f'                <line x1="0" y1="{gy}" x2="{W}" y2="{gy}" stroke="rgba(20,35,48,0.08)" stroke-width="0.8" stroke-dasharray="4 4" />'
        )
    for lon_val, label in ((132.5, "132°30′E"), (133.0, "133°00′E"), (133.5, "133°30′E")):
        gx, _ = proj(lon_val, LAT_MAX)
        graticule_lines.append(
            f'                <line x1="{gx}" y1="0" x2="{gx}" y2="{H}" stroke="rgba(20,35,48,0.08)" stroke-width="0.8" stroke-dasharray="4 4" />'
        )

    # 経緯度ラベルは隣接県ポリゴンの前面に白フチ付きで配置して可読性を確保
    graticule_labels = []
    for lat_val, label in ((33.0, "33°00′N"), (33.5, "33°30′N"), (34.0, "34°00′N")):
        _, gy = proj(LON_MIN, lat_val)
        graticule_labels.append(
            f'                  <text x="12" y="{round(gy - 5, 1)}" fill="#516875" font-size="9.5" font-weight="700" letter-spacing="0.04em" paint-order="stroke fill" stroke="rgba(248,246,240,0.85)" stroke-width="2.5">{label}</text>'
        )
    for lon_val, label in ((132.5, "132°30′E"), (133.0, "133°00′E"), (133.5, "133°30′E")):
        gx, _ = proj(lon_val, LAT_MAX)
        graticule_labels.append(
            f'                  <text x="{round(gx + 5, 1)}" y="18" fill="#516875" font-size="9.5" font-weight="700" letter-spacing="0.04em" paint-order="stroke fill" stroke="rgba(248,246,240,0.85)" stroke-width="2.5">{label}</text>'
        )

    # しまなみ海道（西瀬戸自動車道）の実際のルート座標
    shimanami_pts = [
        proj(132.985, 34.095),  # 今治北IC
        proj(133.035, 34.138),  # 大島（よしうみ）
        proj(133.080, 34.215),  # 伯方島
        proj(133.025, 34.252),  # 大三島
        proj(133.085, 34.310),  # 生口島・尾道方面
    ]
    shimanami_d = "M " + " L ".join(f"{x},{y}" for x, y in shimanami_pts)

    # 縮尺バー（20km相当のピクセル幅）
    km_per_deg_lat = 111.32
    deg_for_20km = 20.0 / km_per_deg_lat
    px_20km = round(deg_for_20km * SCALE, 1)

    neighbor_combined = " ".join(neighbor_paths)
    ehime_outline_combined = " ".join(ehime_all_subpaths)

    # 愛媛の一次産業＆愛犬パトロール隊のイラストデコレーションレイヤー（海・山・肉球ルート）
    decorations_svg = """
                <!-- ========================================================= -->
                <!-- 愛媛の一次産業（みかん段々畑・キウイ・真鯛/シマアジ養殖いけす）＆パトロール隊デコレーション -->
                <!-- ========================================================= -->
                <g class="ehime-map-decorations" aria-hidden="true" pointer-events="none">

                  <!-- 1. 市町をめぐる「愛犬パトロール隊の足跡（肉球）点線ルート」 -->
                  <!-- しまなみ海道〜松山沖〜佐田岬〜宇和海をめぐる海上・沿岸パトロール軌跡 -->
                  <path d="M 480 42 Q 405 110 340 165 Q 250 250 155 362 Q 70 425 115 495 Q 175 565 235 645"
                        fill="none" stroke="rgba(217, 99, 38, 0.34)" stroke-width="1.8" stroke-dasharray="4 7" stroke-linecap="round" />

                  <!-- トコトコ歩く小さな肉球スタンプ群 -->
                  <g fill="#D96326" opacity="0.62">
                    <g transform="translate(432, 82) rotate(-38) scale(0.55)">
                      <circle cx="6.5" cy="7.5" r="2.2"/><circle cx="10.2" cy="5.2" r="2.2"/><circle cx="14.3" cy="5.2" r="2.2"/><circle cx="17.8" cy="7.5" r="2.2"/>
                      <path d="M12.2 9.4c-3.1 0-5.6 2.6-5.6 5.4 0 2.1 1.7 3.5 3.6 3.5.9 0 1.4-.3 2-.3s1.1.3 2 .3c1.9 0 3.6-1.4 3.6-3.5 0-2.8-2.5-5.4-5.6-5.4z"/>
                    </g>
                    <g transform="translate(362, 142) rotate(-42) scale(0.55)">
                      <circle cx="6.5" cy="7.5" r="2.2"/><circle cx="10.2" cy="5.2" r="2.2"/><circle cx="14.3" cy="5.2" r="2.2"/><circle cx="17.8" cy="7.5" r="2.2"/>
                      <path d="M12.2 9.4c-3.1 0-5.6 2.6-5.6 5.4 0 2.1 1.7 3.5 3.6 3.5.9 0 1.4-.3 2-.3s1.1.3 2 .3c1.9 0 3.6-1.4 3.6-3.5 0-2.8-2.5-5.4-5.6-5.4z"/>
                    </g>
                    <g transform="translate(268, 226) rotate(-45) scale(0.55)">
                      <circle cx="6.5" cy="7.5" r="2.2"/><circle cx="10.2" cy="5.2" r="2.2"/><circle cx="14.3" cy="5.2" r="2.2"/><circle cx="17.8" cy="7.5" r="2.2"/>
                      <path d="M12.2 9.4c-3.1 0-5.6 2.6-5.6 5.4 0 2.1 1.7 3.5 3.6 3.5.9 0 1.4-.3 2-.3s1.1.3 2 .3c1.9 0 3.6-1.4 3.6-3.5 0-2.8-2.5-5.4-5.6-5.4z"/>
                    </g>
                    <g transform="translate(198, 305) rotate(-48) scale(0.55)">
                      <circle cx="6.5" cy="7.5" r="2.2"/><circle cx="10.2" cy="5.2" r="2.2"/><circle cx="14.3" cy="5.2" r="2.2"/><circle cx="17.8" cy="7.5" r="2.2"/>
                      <path d="M12.2 9.4c-3.1 0-5.6 2.6-5.6 5.4 0 2.1 1.7 3.5 3.6 3.5.9 0 1.4-.3 2-.3s1.1.3 2 .3c1.9 0 3.6-1.4 3.6-3.5 0-2.8-2.5-5.4-5.6-5.4z"/>
                    </g>
                    <g transform="translate(132, 512) rotate(32) scale(0.55)">
                      <circle cx="6.5" cy="7.5" r="2.2"/><circle cx="10.2" cy="5.2" r="2.2"/><circle cx="14.3" cy="5.2" r="2.2"/><circle cx="17.8" cy="7.5" r="2.2"/>
                      <path d="M12.2 9.4c-3.1 0-5.6 2.6-5.6 5.4 0 2.1 1.7 3.5 3.6 3.5.9 0 1.4-.3 2-.3s1.1.3 2 .3c1.9 0 3.6-1.4 3.6-3.5 0-2.8-2.5-5.4-5.6-5.4z"/>
                    </g>
                    <g transform="translate(192, 588) rotate(35) scale(0.55)">
                      <circle cx="6.5" cy="7.5" r="2.2"/><circle cx="10.2" cy="5.2" r="2.2"/><circle cx="14.3" cy="5.2" r="2.2"/><circle cx="17.8" cy="7.5" r="2.2"/>
                      <path d="M12.2 9.4c-3.1 0-5.6 2.6-5.6 5.4 0 2.1 1.7 3.5 3.6 3.5.9 0 1.4-.3 2-.3s1.1.3 2 .3c1.9 0 3.6-1.4 3.6-3.5 0-2.8-2.5-5.4-5.6-5.4z"/>
                    </g>
                  </g>

                  <!-- 2. 南予（宇和海：宇和島・愛南沖）の「丸い養殖いけす群（真鯛・シマアジ養殖日本一！）」 -->
                  <g transform="translate(178, 592)">
                    <!-- 丸いいけす3基＋波紋 -->
                    <ellipse cx="0" cy="0" rx="18" ry="8" fill="none" stroke="rgba(255,255,255,0.65)" stroke-width="1" stroke-dasharray="3 2" />
                    <ellipse cx="0" cy="0" rx="14" ry="6" fill="#2A6B84" stroke="#E87A2E" stroke-width="2" />
                    <path d="M -5 0 Q 0 -3 5 0 Q 0 2 -5 0 Z" fill="#F48272" />

                    <ellipse cx="34" cy="-9" rx="17" ry="7.5" fill="none" stroke="rgba(255,255,255,0.65)" stroke-width="1" stroke-dasharray="3 2" />
                    <ellipse cx="34" cy="-9" rx="13" ry="5.5" fill="#2A6B84" stroke="#E87A2E" stroke-width="1.8" />
                    <path d="M 29 -9 Q 34 -12 39 -9 Q 34 -7 29 -9 Z" fill="#92D6E8" />

                    <ellipse cx="22" cy="12" rx="18" ry="8" fill="none" stroke="rgba(255,255,255,0.65)" stroke-width="1" stroke-dasharray="3 2" />
                    <ellipse cx="22" cy="12" rx="14" ry="6" fill="#2A6B84" stroke="#E87A2E" stroke-width="2" />
                    <path d="M 17 12 Q 22 9 27 12 Q 22 14 17 12 Z" fill="#F48272" />

                    <!-- 手書き風極小ラベル -->
                    <rect x="-56" y="25" width="154" height="20" rx="10" fill="rgba(252,251,247,0.9)" stroke="rgba(42,107,132,0.35)" stroke-width="1" />
                    <text x="21" y="38.5" text-anchor="middle" fill="#184C63" font-size="9.2" font-weight="700">🐟 真鯛・シマアジ養殖日本一！</text>
                  </g>

                  <!-- 3. 東予（今治・しまなみ＆燧灘沖）の養殖いけすと白い渡り船・漁船 -->
                  <g transform="translate(592, 166)">
                    <ellipse cx="0" cy="0" rx="15" ry="6.5" fill="none" stroke="rgba(255,255,255,0.65)" stroke-width="1" stroke-dasharray="3 2" />
                    <ellipse cx="0" cy="0" rx="11.5" ry="4.8" fill="#2A6B84" stroke="#E87A2E" stroke-width="1.7" />
                    <ellipse cx="26" cy="7" rx="14" ry="6" fill="none" stroke="rgba(255,255,255,0.65)" stroke-width="1" stroke-dasharray="3 2" />
                    <ellipse cx="26" cy="7" rx="10.5" ry="4.4" fill="#2A6B84" stroke="#E87A2E" stroke-width="1.7" />
                    <text x="14" y="24" text-anchor="middle" fill="#23576E" font-size="8.6" font-weight="700" paint-order="stroke fill" stroke="rgba(248,246,240,0.85)" stroke-width="2.5">燧灘の養殖いけす</text>
                  </g>

                  <!-- しまなみ海道の島々のそばを進む小さな渡り船・漁船 -->
                  <g transform="translate(415, 92)">
                    <path d="M -24 5 Q -10 3 2 3" fill="none" stroke="#FFFFFF" stroke-width="1.5" stroke-dasharray="2 2" />
                    <polygon points="0,2 18,2 22,-3 -3,-3" fill="#FFFDF8" stroke="#1E3B4D" stroke-width="1.1" />
                    <rect x="4" y="-8" width="8" height="5" rx="1" fill="#FFFDF8" stroke="#1E3B4D" stroke-width="1" />
                    <polygon points="8,-12 15,-10 8,-8" fill="#D96326" />
                  </g>

                  <!-- 伊予灘を進む小さな白い漁船 -->
                  <g transform="translate(235, 252)">
                    <path d="M 24 4 Q 36 3 46 4" fill="none" stroke="#FFFFFF" stroke-width="1.4" stroke-dasharray="2 2" />
                    <polygon points="0,2 18,2 21,-2 -3,-2" fill="#FFFDF8" stroke="#1E3B4D" stroke-width="1.1" />
                    <rect x="5" y="-7" width="7" height="5" rx="1" fill="#FFFDF8" stroke="#1E3B4D" stroke-width="1" />
                    <polygon points="8,-11 2,-9 8,-7" fill="#D96326" />
                  </g>

                  <!-- 4. 八幡浜・伊方・西予沿岸の「みかん・柑橘の段々畑🍊」ミニイラスト -->
                  <g transform="translate(212, 352)">
                    <!-- 段々畑のグリーン＆石垣アーチ -->
                    <path d="M -22 8 Q 0 -2 22 8" fill="none" stroke="#5B8746" stroke-width="3" stroke-linecap="round" />
                    <path d="M -18 14 Q 0 5 18 14" fill="none" stroke="#789E54" stroke-width="2.6" stroke-linecap="round" />
                    <!-- オレンジのみかんの実 -->
                    <circle cx="-12" cy="3" r="4" fill="#F27924" stroke="#FFF8EB" stroke-width="0.8" />
                    <circle cx="0" cy="0" r="4.3" fill="#FF9838" stroke="#FFF8EB" stroke-width="0.8" />
                    <circle cx="12" cy="3" r="4" fill="#F27924" stroke="#FFF8EB" stroke-width="0.8" />
                    <circle cx="-6" cy="9" r="3.8" fill="#FF9838" stroke="#FFF8EB" stroke-width="0.8" />
                    <circle cx="7" cy="9" r="3.8" fill="#F27924" stroke="#FFF8EB" stroke-width="0.8" />
                    <rect x="-54" y="-22" width="108" height="19" rx="9.5" fill="rgba(252,251,247,0.92)" stroke="rgba(217,99,38,0.4)" stroke-width="1" />
                    <text x="0" y="-9.5" text-anchor="middle" fill="#B54B16" font-size="9" font-weight="700">🍊 みかんの段々畑</text>
                  </g>

                  <!-- 5. 中予〜東予（伊予・東温・西条・山沿い）の「キウイ王国🥝」ミニイラスト -->
                  <g transform="translate(546, 378)">
                    <!-- キウイのつる棚とグリーンの実 -->
                    <path d="M -24 0 Q 0 -7 24 0" fill="none" stroke="#6E4E35" stroke-width="2" stroke-linecap="round" />
                    <circle cx="-14" cy="5" r="4.5" fill="#75B848" stroke="#4D3620" stroke-width="1" />
                    <circle cx="-3" cy="6" r="4.5" fill="#9E7B46" stroke="#4D3620" stroke-width="1" />
                    <circle cx="8" cy="5" r="4.8" fill="#8CD156" stroke="#4D3620" stroke-width="1" />
                    <circle cx="8" cy="5" r="1.8" fill="#EAF7D5" />
                    <circle cx="19" cy="6" r="4.5" fill="#9E7B46" stroke="#4D3620" stroke-width="1" />
                    <rect x="-52" y="15" width="108" height="19" rx="9.5" fill="rgba(252,251,247,0.92)" stroke="rgba(74,107,93,0.4)" stroke-width="1" />
                    <text x="2" y="27.5" text-anchor="middle" fill="#3A6347" font-size="9" font-weight="700">🥝 キウイ生産日本一</text>
                  </g>

                  <!-- 6. パトロール隊のミニ犬イラスト（しまなみ海道入口 ＆ 佐田岬半島先端） -->
                  <!-- (A) しまなみ海道入口で見守る「隊長みろく」ミニイラスト -->
                  <g transform="translate(476, 34)">
                    <circle cx="0" cy="0" r="15" fill="#FCFBF7" stroke="#D96326" stroke-width="1.8" />
                    <!-- 柴犬の三角耳と顔 -->
                    <polygon points="-9,-5 -11,-14 -3,-9" fill="#D88C4A" stroke="#4A2E1B" stroke-width="1.2" />
                    <polygon points="9,-5 11,-14 3,-9" fill="#D88C4A" stroke="#4A2E1B" stroke-width="1.2" />
                    <ellipse cx="0" cy="1" rx="10" ry="8.5" fill="#D88C4A" stroke="#4A2E1B" stroke-width="1.2" />
                    <path d="M -8 2 Q 0 -1 8 2 C 7 7, -7 7, -8 2 Z" fill="#FCF6EA" />
                    <circle cx="-3.8" cy="-1" r="1.3" fill="#231815" />
                    <circle cx="3.8" cy="-1" r="1.3" fill="#231815" />
                    <ellipse cx="0" cy="2" rx="2" ry="1.4" fill="#231815" />
                    <path d="M -2 5 L -2 8.5 C -2 10, 2 10, 2 8.5 L 2 5 Z" fill="#F08A99" />
                    <rect x="18" y="-9" width="96" height="18" rx="9" fill="#142330" />
                    <text x="66" y="3" text-anchor="middle" fill="#FCFBF7" font-size="8.5" font-weight="700">しまなみ巡回中🐾</text>
                  </g>

                  <!-- (B) 佐田岬半島の先頭を走る「副隊長JIN＆ハク」ミニシルエット -->
                  <g transform="translate(68, 434)">
                    <circle cx="0" cy="0" r="14" fill="#FCFBF7" stroke="#4A6B5D" stroke-width="1.6" />
                    <!-- 垂れ耳チワックスJINの顔 -->
                    <path d="M -7 -5 C -13 -5, -14 3, -9 6 Z" fill="#A6612A" />
                    <path d="M 7 -5 C 13 -5, 14 3, 9 6 Z" fill="#A6612A" />
                    <ellipse cx="0" cy="0" rx="8.5" ry="7.5" fill="#D48C4B" stroke="#4A2E1B" stroke-width="1.1" />
                    <circle cx="-3.2" cy="-1" r="1.4" fill="#1E1410" />
                    <circle cx="3.2" cy="-1" r="1.4" fill="#1E1410" />
                    <ellipse cx="0" cy="2" rx="1.8" ry="1.3" fill="#1E1410" />
                    <rect x="-36" y="17" width="92" height="17" rx="8.5" fill="rgba(252,251,247,0.92)" stroke="#4A6B5D" stroke-width="1" />
                    <text x="10" y="28.5" text-anchor="middle" fill="#142330" font-size="8.3" font-weight="700">佐田岬パトロール🐾</text>
                  </g>
                </g>"""

    svg = f"""              <svg id="ehime-map" viewBox="0 0 {W} {H}" role="img" aria-label="国土数値情報GISデータに基づく愛媛県全20市町インタラクティブ地図">
                <defs>
                  <linearGradient id="seaGrad" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stop-color="#DCE8EE" />
                    <stop offset="55%" stop-color="#D2E2E9" />
                    <stop offset="100%" stop-color="#C8DAE3" />
                  </linearGradient>
                  <filter id="landShadow" x="-5%" y="-5%" width="110%" height="110%">
                    <feDropShadow dx="0" dy="2" stdDeviation="3.5" flood-color="#0E2231" flood-opacity="0.16" />
                  </filter>
                </defs>

                <!-- 瀬戸内海・宇和海 海洋レイヤー -->
                <rect width="{W}" height="{H}" fill="url(#seaGrad)" />

                <!-- 経緯度グリッド（Graticule） -->
{chr(10).join(graticule_lines)}

                <!-- 周辺県（広島・山口・香川・徳島・高知・大分）の海岸線・陸地コンテキスト -->
                <g class="neighbor-land" aria-hidden="true">
                  <path d="{neighbor_combined}" fill="#E6E2D6" stroke="#C8C2B2" stroke-width="0.9" stroke-linejoin="round" />
                  <text x="385" y="34" fill="#7D786A" font-size="11" font-weight="700" letter-spacing="0.18em" paint-order="stroke fill" stroke="rgba(248,246,240,0.8)" stroke-width="2.5">広 島 県</text>
                  <text x="80" y="75" fill="#7D786A" font-size="11" font-weight="700" letter-spacing="0.18em" paint-order="stroke fill" stroke="rgba(248,246,240,0.8)" stroke-width="2.5">山口県</text>
                  <text x="795" y="175" fill="#7D786A" font-size="11" font-weight="700" letter-spacing="0.16em" paint-order="stroke fill" stroke="rgba(248,246,240,0.8)" stroke-width="2.5">香川県</text>
                  <text x="785" y="295" fill="#7D786A" font-size="11" font-weight="700" letter-spacing="0.16em" paint-order="stroke fill" stroke="rgba(248,246,240,0.8)" stroke-width="2.5">徳島県</text>
                  <text x="565" y="485" fill="#7D786A" font-size="12" font-weight="700" letter-spacing="0.22em" paint-order="stroke fill" stroke="rgba(248,246,240,0.8)" stroke-width="2.5">高 知 県</text>
                  <text x="36" y="540" fill="#7D786A" font-size="11" font-weight="700" letter-spacing="0.16em" paint-order="stroke fill" stroke="rgba(248,246,240,0.8)" stroke-width="2.5">大分県</text>
                </g>

                <!-- 経緯度ラベル（最前面の地図枠内目盛り） -->
                <g class="graticule-labels" aria-hidden="true">
{chr(10).join(graticule_labels)}
                </g>

                <!-- 海域名称（カルトグラフィ表記） -->
                <g class="sea-labels" aria-hidden="true">
                  <text x="210" y="215" fill="#5A7788" font-size="13.5" font-weight="500" letter-spacing="0.28em">伊 予 灘</text>
                  <text x="615" y="138" fill="#5A7788" font-size="13.5" font-weight="500" letter-spacing="0.28em">燧 灘（ひうちなだ）</text>
                  <text x="305" y="95" fill="#5A7788" font-size="12" font-weight="500" letter-spacing="0.22em">安 芸 灘</text>
                  <text x="155" y="545" fill="#5A7788" font-size="13.5" font-weight="500" letter-spacing="0.28em">宇 和 海</text>
                  <text x="95" y="485" fill="#648191" font-size="11" font-weight="500" letter-spacing="0.18em">豊後水道</text>
                </g>

                <!-- しまなみ海道（西瀬戸自動車道）ルート線 -->
                <path d="{shimanami_d}" fill="none" stroke="#6E92A4" stroke-width="1.6" stroke-dasharray="3 3" />

                <!-- 愛媛県全体の海岸線ドロップシャドウベース -->
                <path d="{ehime_outline_combined}" fill="#F8F6F0" filter="url(#landShadow)" pointer-events="none" />

                <!-- 愛媛県全20市町 行政区域GISポリゴン -->
{chr(10).join(muni_groups)}
{decorations_svg}

                <!-- 方位記号（コンパスローズ）＆ 20km縮尺スケールバー（国土地理院・地図帳スタイル） -->
                <g class="map-carto-hud" transform="translate(755, 628)" aria-hidden="true">
                  <rect x="-52" y="-36" width="142" height="76" rx="8" fill="rgba(252,251,247,0.88)" stroke="rgba(20,35,48,0.16)" stroke-width="1" />
                  <!-- 方位針 -->
                  <g transform="translate(-24, 2)">
                    <polygon points="0,-20 5,-4 0,-8 -5,-4" fill="#142330" />
                    <polygon points="0,16 5,0 0,4 -5,0" fill="#8596A2" />
                    <text x="0" y="-23" text-anchor="middle" fill="#142330" font-size="9.5" font-weight="700">N</text>
                  </g>
                  <!-- 20kmスケール -->
                  <g transform="translate(4, -6)">
                    <line x1="0" y1="12" x2="{px_20km}" y2="12" stroke="#142330" stroke-width="2" />
                    <line x1="0" y1="7" x2="0" y2="15" stroke="#142330" stroke-width="1.5" />
                    <line x1="{round(px_20km/2, 1)}" y1="9" x2="{round(px_20km/2, 1)}" y2="14" stroke="#142330" stroke-width="1.2" />
                    <line x1="{px_20km}" y1="7" x2="{px_20km}" y2="15" stroke="#142330" stroke-width="1.5" />
                    <text x="0" y="3" fill="#314556" font-size="8.5" font-weight="700">0</text>
                    <text x="{px_20km}" y="3" text-anchor="end" fill="#314556" font-size="8.5" font-weight="700">20km</text>
                    <text x="0" y="27" fill="#5F6F7C" font-size="7.8" font-weight="500">国土数値情報GIS準拠</text>
                  </g>
                </g>
              </svg>"""
    return svg


def main():
    svg_html = generate_svg()
    html_text = INDEX_HTML_PATH.read_text(encoding="utf-8")
    new_html, n = re.subn(
        r'<svg id="ehime-map"[\s\S]*?</svg>',
        svg_html.strip(),
        html_text,
        count=1,
    )
    if n != 1:
        raise RuntimeError("index.html 内の <svg id='ehime-map'> が見つかりませんでした。")
    INDEX_HTML_PATH.write_text(new_html, encoding="utf-8")
    print(f"Updated {INDEX_HTML_PATH} with GIS SVG map ({len(svg_html):,} bytes)")


if __name__ == "__main__":
    main()
