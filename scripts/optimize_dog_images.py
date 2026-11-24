#!/usr/bin/env python3
"""
愛犬3兄弟（みろく・JIN・ハク）の画像を検出し、長辺800pxにリサイズ＋WebP軽量化して
`web/assets/dogs/{miroku,jin,haku}_web.webp` に保存するスクリプト。
※元画像は一切削除せず保持する。
"""

import base64
import mimetypes
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "web" / "assets"
DOGS_DIR = ASSETS_DIR / "dogs"

TARGETS = [
    ("miroku", ["miroku.png", "miroku.jpg", "miroku.jpeg", "miroku.webp", "dog1.png", "dog1.jpg", "dog1.jpeg"]),
    ("jin", ["jin.png", "jin.jpg", "jin.jpeg", "jin.webp", "dog2.png", "dog2.jpg", "dog2.jpeg"]),
    ("haku", ["haku.jpg", "haku.jpeg", "haku.png", "haku.webp", "dog3.png", "dog3.jpg", "dog3.jpeg"]),
]


def find_source_image(candidates: list[str]) -> Path | None:
    # 1. web/assets/dogs/ 内を優先探索
    for name in candidates:
        p = DOGS_DIR / name
        if p.exists():
            return p
    # 2. web/assets/ 直下も探索し、見つかれば web/assets/dogs/ へもコピー（元ファイルは削除しない）
    for name in candidates:
        p = ASSETS_DIR / name
        if p.exists():
            DOGS_DIR.mkdir(parents=True, exist_ok=True)
            dst = DOGS_DIR / name
            if not dst.exists():
                shutil.copy2(p, dst)
            return dst
    return None


def convert_to_webp_800(page, src_path: Path, out_path: Path, quality: float = 0.84) -> tuple[int, int, int]:
    mime, _ = mimetypes.guess_type(str(src_path))
    if not mime:
        mime = "image/png"
    b64 = base64.b64encode(src_path.read_bytes()).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    res = page.evaluate(
        """async ({ dataUrl, maxSide, quality }) => {
            const img = new Image();
            await new Promise((resolve, reject) => {
                img.onload = resolve;
                img.onerror = reject;
                img.src = dataUrl;
            });
            const w0 = img.naturalWidth;
            const h0 = img.naturalHeight;
            let w = w0;
            let h = h0;
            if (Math.max(w0, h0) > maxSide) {
                if (w0 >= h0) {
                    w = maxSide;
                    h = Math.round((h0 * maxSide) / w0);
                } else {
                    h = maxSide;
                    w = Math.round((w0 * maxSide) / h0);
                }
            }
            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d');
            ctx.imageSmoothingEnabled = true;
            ctx.imageSmoothingQuality = 'high';
            ctx.drawImage(img, 0, 0, w, h);
            const webpUrl = canvas.toDataURL('image/webp', quality);
            return { webpB64: webpUrl.split(',')[1], width: w, height: h, origW: w0, origH: h0 };
        }""",
        {"dataUrl": data_url, "maxSide": 800, "quality": quality},
    )
    webp_bytes = base64.b64decode(res["webpB64"])
    out_path.write_bytes(webp_bytes)
    return res["width"], res["height"], len(webp_bytes)


def main():
    DOGS_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page()
        page.set_content("<!DOCTYPE html><html><body></body></html>")
        for key, candidates in TARGETS:
            src = find_source_image(candidates)
            out_fp = DOGS_DIR / f"{key}_web.webp"
            if src is None:
                raise FileNotFoundError(f"{key} の元画像が見つかりません: {candidates}")
            orig_size = src.stat().st_size
            w, h, new_size = convert_to_webp_800(page, src, out_fp)
            ratio = (1.0 - new_size / orig_size) * 100.0 if orig_size else 0.0
            print(
                f"✓ {key}: {src.name} ({orig_size:,} bytes) -> {out_fp.relative_to(BASE_DIR)} "
                f"({w}x{h}px, {new_size:,} bytes / -{ratio:.1f}%)"
            )
        browser.close()


if __name__ == "__main__":
    main()
