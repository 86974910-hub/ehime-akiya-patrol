#!/usr/bin/env python3
"""
エディトリアル水彩・色鉛筆タッチの「愛媛の自然（みかん段々畑・キウイ棚・宇和海の真鯛/シマアジ養殖いけす・古民家・しまなみ海道）
× 愛犬3兄弟パトロール隊（みろく・JIN・ハク）」のヒーローカバーイラスト（`web/assets/hero_illustration.png`）を
高精細HTML5 Canvas/SVG合成＋Playwrightで生成するスクリプト。
"""

from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_PNG = BASE_DIR / "web" / "assets" / "hero_illustration.png"

ILLUSTRATION_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8" />
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@500;700&display=swap" rel="stylesheet" />
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    width: 960px;
    height: 760px;
    overflow: hidden;
    background: #FBF7EE;
    font-family: "Noto Sans JP", -apple-system, BlinkMacSystemFont, sans-serif;
  }
  .canvas-frame {
    position: relative;
    width: 960px;
    height: 760px;
    background: linear-gradient(180deg, #FDF8EE 0%, #F7EFE0 100%);
  }
  svg.hero-art {
    width: 100%;
    height: 100%;
    display: block;
  }
</style>
</head>
<body>
<div class="canvas-frame">
  <svg class="hero-art" viewBox="0 0 960 760" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <!-- 水彩紙のやわらかなテクスチャ＆水彩のにじみフィルタ -->
      <filter id="watercolor" x="-5%" y="-5%" width="110%" height="110%">
        <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="3" result="noise" />
        <feDisplacementMap in="SourceGraphic" in2="noise" scale="4.5" xChannelSelector="R" yChannelSelector="G" />
      </filter>
      <filter id="pencilSketch" x="-5%" y="-5%" width="110%" height="110%">
        <feTurbulence type="fractalNoise" baseFrequency="0.6" numOctaves="2" result="fine" />
        <feDisplacementMap in="SourceGraphic" in2="fine" scale="1.2" />
      </filter>
      <filter id="softGlow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="12" result="blur" />
        <feMerge>
          <feMergeNode in="blur" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
      <filter id="cardShadow" x="-10%" y="-10%" width="120%" height="120%">
        <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#142330" flood-opacity="0.16" />
      </filter>

      <!-- 空と瀬戸内・宇和海のグラデーション -->
      <linearGradient id="skyGrad" x1="0" y1="0" x2="0.2" y2="1">
        <stop offset="0%" stop-color="#FCECD5" />
        <stop offset="48%" stop-color="#F8E2C4" />
        <stop offset="100%" stop-color="#E5F0F2" />
      </linearGradient>
      <radialGradient id="sunGlow" cx="0.76" cy="0.18" r="0.35">
        <stop offset="0%" stop-color="#FFF7E2" stop-opacity="0.95" />
        <stop offset="50%" stop-color="#FAD49B" stop-opacity="0.55" />
        <stop offset="100%" stop-color="#FAD49B" stop-opacity="0" />
      </radialGradient>
      <linearGradient id="seaWater" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="#4A8FA8" />
        <stop offset="45%" stop-color="#2E7490" />
        <stop offset="100%" stop-color="#1B5570" />
      </linearGradient>
      <linearGradient id="terraceHill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#8AB060" />
        <stop offset="100%" stop-color="#5B8746" />
      </linearGradient>
      <linearGradient id="roadGrad" x1="0" y1="0" x2="0.8" y2="1">
        <stop offset="0%" stop-color="#F3E5CE" />
        <stop offset="50%" stop-color="#E7D4B5" />
        <stop offset="100%" stop-color="#D7BE98" />
      </linearGradient>
    </defs>

    <!-- 1. 瀬戸内の朝焼け空と太陽の光 -->
    <rect width="960" height="760" fill="url(#skyGrad)" />
    <circle cx="730" cy="135" r="240" fill="url(#sunGlow)" />
    <circle cx="730" cy="135" r="56" fill="#FFFDF6" opacity="0.88" filter="url(#softGlow)" />

    <!-- 遠景の島々（しまなみ海道・忽那諸島）と白い斜張橋 -->
    <g filter="url(#watercolor)" opacity="0.85">
      <path d="M 40 245 Q 115 190 210 232 T 360 250 L 360 270 L 40 270 Z" fill="#87A9B5" />
      <path d="M 520 238 Q 610 182 705 225 T 880 235 L 920 265 L 520 265 Z" fill="#779BA9" />
      <path d="M 740 220 Q 830 168 940 218 L 960 255 L 740 255 Z" fill="#638797" />
    </g>

    <!-- しまなみ海道（白い吊り橋・斜張橋） -->
    <g stroke="#FAF6EE" stroke-width="2" fill="none" opacity="0.92">
      <line x1="340" y1="242" x2="555" y2="242" stroke-width="2.8" />
      <path d="M 385 252 L 385 194 L 392 194 L 392 252" stroke-width="2.4" />
      <path d="M 495 252 L 495 194 L 502 194 L 502 252" stroke-width="2.4" />
      <!-- 斜張ケーブル -->
      <line x1="388" y1="198" x2="348" y2="242" stroke-width="1.1" />
      <line x1="388" y1="206" x2="362" y2="242" stroke-width="1.1" />
      <line x1="388" y1="198" x2="438" y2="242" stroke-width="1.1" />
      <line x1="388" y1="206" x2="418" y2="242" stroke-width="1.1" />
      <line x1="498" y1="198" x2="448" y2="242" stroke-width="1.1" />
      <line x1="498" y1="206" x2="468" y2="242" stroke-width="1.1" />
      <line x1="498" y1="198" x2="545" y2="242" stroke-width="1.1" />
      <line x1="498" y1="206" x2="525" y2="242" stroke-width="1.1" />
    </g>

    <!-- 2. 瀬戸内海・宇和海のきらめく海面 -->
    <path d="M 0 248 Q 480 238 960 248 L 960 510 L 0 510 Z" fill="url(#seaWater)" />

    <!-- 海面の光と穏やかな波紋 -->
    <g stroke="#FCEFD9" stroke-linecap="round" opacity="0.55" fill="none">
      <path d="M 620 272 Q 665 268 710 272" stroke-width="2.2" />
      <path d="M 650 288 Q 695 284 745 288" stroke-width="1.8" />
      <path d="M 590 306 Q 635 302 680 306" stroke-width="1.6" />
      <path d="M 120 278 Q 165 274 210 278" stroke-width="1.8" />
      <path d="M 260 305 Q 310 300 355 305" stroke-width="1.6" />
      <path d="M 760 330 Q 815 325 870 330" stroke-width="1.8" />
    </g>

    <!-- 3. 宇和海の丸い魚養殖いけす（真鯛・シマアジ養殖場） -->
    <g transform="translate(565, 336)">
      <!-- いけす外周の波紋とフロートリング（5基） -->
      <g>
        <ellipse cx="0" cy="0" rx="44" ry="18" fill="none" stroke="rgba(255,255,255,0.38)" stroke-width="1.4" stroke-dasharray="5 4" />
        <ellipse cx="0" cy="0" rx="35" ry="14" fill="#174960" stroke="#F08A38" stroke-width="3.2" />
        <ellipse cx="0" cy="0" rx="29" ry="11" fill="none" stroke="#FCE8C8" stroke-width="1" stroke-dasharray="3 3" />
        <!-- 跳ねる真鯛とシマアジのシルエット -->
        <path d="M -12 1 Q -2 -7 10 0 Q 2 5 -12 1 Z" fill="#F2786B" opacity="0.9" />
        <polygon points="10,0 16,-4 15,3" fill="#F2786B" />
        <path d="M 4 -4 Q 14 -10 22 -3 Q 14 1 4 -4 Z" fill="#8CD2E6" opacity="0.9" />
      </g>
      <g transform="translate(96, -14)">
        <ellipse cx="0" cy="0" rx="42" ry="17" fill="none" stroke="rgba(255,255,255,0.38)" stroke-width="1.4" stroke-dasharray="5 4" />
        <ellipse cx="0" cy="0" rx="33" ry="13" fill="#174960" stroke="#F08A38" stroke-width="3" />
        <ellipse cx="0" cy="0" rx="27" ry="10" fill="none" stroke="#FCE8C8" stroke-width="1" stroke-dasharray="3 3" />
        <path d="M -10 0 Q 0 -6 10 1 Q 0 4 -10 0 Z" fill="#F2786B" opacity="0.9" />
      </g>
      <g transform="translate(190, -26)">
        <ellipse cx="0" cy="0" rx="38" ry="15" fill="none" stroke="rgba(255,255,255,0.35)" stroke-width="1.3" stroke-dasharray="4 4" />
        <ellipse cx="0" cy="0" rx="30" ry="11.5" fill="#174960" stroke="#F08A38" stroke-width="2.8" />
        <path d="M -8 0 Q 2 -5 10 1 Q 1 4 -8 0 Z" fill="#8CD2E6" opacity="0.9" />
      </g>
      <g transform="translate(55, 28)">
        <ellipse cx="0" cy="0" rx="46" ry="19" fill="none" stroke="rgba(255,255,255,0.4)" stroke-width="1.4" stroke-dasharray="5 4" />
        <ellipse cx="0" cy="0" rx="37" ry="15" fill="#174960" stroke="#F08A38" stroke-width="3.2" />
        <ellipse cx="0" cy="0" rx="31" ry="12" fill="none" stroke="#FCE8C8" stroke-width="1" stroke-dasharray="3 3" />
        <path d="M -14 2 Q -2 -7 11 0 Q 1 5 -14 2 Z" fill="#F2786B" opacity="0.95" />
        <path d="M 2 -3 Q 11 -9 20 -2 Q 11 2 2 -3 Z" fill="#8CD2E6" opacity="0.9" />
      </g>
      <g transform="translate(156, 15)">
        <ellipse cx="0" cy="0" rx="44" ry="18" fill="none" stroke="rgba(255,255,255,0.38)" stroke-width="1.4" stroke-dasharray="5 4" />
        <ellipse cx="0" cy="0" rx="35" ry="14" fill="#174960" stroke="#F08A38" stroke-width="3" />
        <path d="M -9 1 Q 1 -6 11 0 Q 2 4 -9 1 Z" fill="#F2786B" opacity="0.9" />
      </g>
    </g>

    <!-- 白い漁船と航跡（引き波） -->
    <g transform="translate(415, 292)">
      <path d="M -65 14 Q -28 8 4 8" fill="none" stroke="#FFFDF8" stroke-width="2.2" stroke-linecap="round" opacity="0.78" />
      <path d="M -52 21 Q -20 15 2 11" fill="none" stroke="#FFFDF8" stroke-width="1.5" stroke-dasharray="4 4" opacity="0.65" />
      <polygon points="0,6 38,6 46,-2 -6,-2" fill="#FFFDF8" stroke="#1E3B4D" stroke-width="1.5" />
      <rect x="8" y="-12" width="16" height="10" rx="2" fill="#FFFDF8" stroke="#1E3B4D" stroke-width="1.4" />
      <rect x="11" y="-9" width="10" height="4" fill="#5CA0B8" />
      <line x1="18" y1="-12" x2="18" y2="-22" stroke="#1E3B4D" stroke-width="1.5" />
      <polygon points="18,-22 29,-18 18,-15" fill="#D96326" />
    </g>

    <!-- 4. 愛媛の段々畑（みかん・柑橘＆キウイ棚）と古民家のある里山 -->
    <!-- 奥の里山と古民家 -->
    <g filter="url(#watercolor)">
      <path d="M 0 325 Q 175 275 360 342 Q 490 390 610 420 L 0 450 Z" fill="#6F9951" />
      <!-- 石垣の段々畑ライン（3段） -->
      <path d="M 0 365 Q 185 320 390 385 L 390 402 Q 185 338 0 382 Z" fill="#C7BCA6" stroke="#8C806B" stroke-width="1.2" />
      <path d="M 0 412 Q 210 368 450 432 L 450 450 Q 210 386 0 428 Z" fill="#BEB29B" stroke="#8C806B" stroke-width="1.2" />
    </g>

    <!-- 瓦屋根の古民家（瀬戸内の里山古民家） -->
    <g transform="translate(88, 258)" filter="url(#pencilSketch)">
      <!-- 母屋の白壁・木組み -->
      <rect x="18" y="38" width="88" height="46" fill="#F7F1E3" stroke="#3D2E22" stroke-width="2" />
      <line x1="48" y1="38" x2="48" y2="84" stroke="#5A4231" stroke-width="2.5" />
      <line x1="78" y1="38" x2="78" y2="84" stroke="#5A4231" stroke-width="2.5" />
      <rect x="24" y="48" width="18" height="24" fill="#D9B782" stroke="#5A4231" stroke-width="1.5" />
      <rect x="54" y="48" width="18" height="28" fill="#E8CFA4" stroke="#5A4231" stroke-width="1.5" />
      <rect x="84" y="50" width="16" height="18" fill="#89A8B2" stroke="#5A4231" stroke-width="1.5" />
      <!-- 伝統的な日本瓦の入母屋屋根 -->
      <polygon points="4,42 62,4 120,42" fill="#324350" stroke="#1B2832" stroke-width="2.2" stroke-linejoin="round" />
      <path d="M 12 36 L 112 36" stroke="#5B7080" stroke-width="2" />
      <path d="M 24 28 L 100 28" stroke="#5B7080" stroke-width="1.8" />
      <path d="M 38 20 L 86 20" stroke="#5B7080" stroke-width="1.6" />
      <rect x="50" y="2" width="24" height="5" rx="2" fill="#1B2832" />
    </g>

    <!-- 段々畑にたわわに実る「愛媛みかん・柑橘」の木々 -->
    <g id="mikan-groves">
      <!-- 木1 -->
      <g transform="translate(225, 315)">
        <path d="M -3 28 L -3 8 L 4 8 L 4 28 Z" fill="#6E4B32" />
        <ellipse cx="0" cy="0" rx="28" ry="22" fill="#3F703D" />
        <ellipse cx="-12" cy="4" rx="18" ry="15" fill="#4E8548" />
        <ellipse cx="14" cy="3" rx="18" ry="15" fill="#4E8548" />
        <circle cx="-14" cy="-4" r="5.5" fill="#F27924" stroke="#FFF3D6" stroke-width="0.8" />
        <circle cx="2" cy="-9" r="6" fill="#F5872E" stroke="#FFF3D6" stroke-width="0.8" />
        <circle cx="15" cy="-2" r="5.5" fill="#F27924" stroke="#FFF3D6" stroke-width="0.8" />
        <circle cx="-6" cy="7" r="5.8" fill="#FF9838" stroke="#FFF3D6" stroke-width="0.8" />
        <circle cx="9" cy="8" r="5.5" fill="#F27924" stroke="#FFF3D6" stroke-width="0.8" />
      </g>
      <!-- 木2 -->
      <g transform="translate(292, 332)">
        <path d="M -3 26 L -3 8 L 4 8 L 4 26 Z" fill="#6E4B32" />
        <ellipse cx="0" cy="0" rx="26" ry="20" fill="#3A6938" />
        <ellipse cx="8" cy="2" rx="18" ry="14" fill="#4D8447" />
        <circle cx="-11" cy="-3" r="5.2" fill="#F5872E" />
        <circle cx="4" cy="-7" r="5.8" fill="#FF9838" />
        <circle cx="14" cy="3" r="5.2" fill="#F27924" />
        <circle cx="-3" cy="6" r="5.4" fill="#F27924" />
      </g>
      <!-- 木3 -->
      <g transform="translate(155, 355)">
        <path d="M -3 26 L -3 8 L 4 8 L 4 26 Z" fill="#6E4B32" />
        <ellipse cx="0" cy="0" rx="29" ry="22" fill="#3F703D" />
        <circle cx="-15" cy="-2" r="5.8" fill="#FF9838" />
        <circle cx="0" cy="-9" r="6" fill="#F27924" />
        <circle cx="15" cy="-3" r="5.5" fill="#F5872E" />
        <circle cx="-6" cy="8" r="5.5" fill="#F27924" />
        <circle cx="10" cy="7" r="5.8" fill="#FF9838" />
      </g>
      <!-- 木4 -->
      <g transform="translate(352, 358)">
        <path d="M -3 26 L -3 8 L 4 8 L 4 26 Z" fill="#6E4B32" />
        <ellipse cx="0" cy="0" rx="25" ry="19" fill="#447841" />
        <circle cx="-10" cy="-3" r="5.5" fill="#F27924" />
        <circle cx="6" cy="-6" r="5.5" fill="#FF9838" />
        <circle cx="11" cy="5" r="5.2" fill="#F27924" />
        <circle cx="-3" cy="6" r="5.2" fill="#F5872E" />
      </g>
    </g>

    <!-- 日本一の「キウイフルーツ棚」（手前左〜右上のパーゴラ棚から下がるキウイとつる葉） -->
    <g id="kiwi-trellis" transform="translate(36, 378)">
      <!-- 木製のパーゴラ棚柱と梁 -->
      <line x1="12" y1="68" x2="12" y2="0" stroke="#6E4E35" stroke-width="5" stroke-linecap="round" />
      <line x1="175" y1="76" x2="175" y2="8" stroke="#6E4E35" stroke-width="5" stroke-linecap="round" />
      <line x1="0" y1="4" x2="195" y2="12" stroke="#7A573D" stroke-width="5" stroke-linecap="round" />
      <!-- 豊かなキウイの緑葉 -->
      <ellipse cx="35" cy="6" rx="26" ry="12" fill="#5D9648" />
      <ellipse cx="78" cy="8" rx="30" ry="14" fill="#6AA652" />
      <ellipse cx="122" cy="10" rx="28" ry="13" fill="#558C40" />
      <ellipse cx="162" cy="12" rx="25" ry="12" fill="#6AA652" />
      <!-- 棚からぶら下がるキウイフルーツの実と輪切り断面 -->
      <g transform="translate(32, 22)">
        <ellipse cx="0" cy="0" rx="8" ry="10" fill="#9E7B46" stroke="#5A4222" stroke-width="1.2" />
        <ellipse cx="22" cy="3" rx="8" ry="10" fill="#A6824C" stroke="#5A4222" stroke-width="1.2" />
        <!-- エメラルドグリーンのキウイ断面 -->
        <circle cx="48" cy="4" r="10" fill="#8CD156" stroke="#7A5A2F" stroke-width="2" />
        <circle cx="48" cy="4" r="4" fill="#EAF7D5" />
        <circle cx="48" cy="-1" r="0.9" fill="#222" /><circle cx="52" cy="2" r="0.9" fill="#222" />
        <circle cx="52" cy="6" r="0.9" fill="#222" /><circle cx="48" cy="9" r="0.9" fill="#222" />
        <circle cx="44" cy="6" r="0.9" fill="#222" /><circle cx="44" cy="2" r="0.9" fill="#222" />
        <ellipse cx="76" cy="5" rx="8" ry="10" fill="#9E7B46" stroke="#5A4222" stroke-width="1.2" />
        <ellipse cx="104" cy="7" rx="8.5" ry="10.5" fill="#A6824C" stroke="#5A4222" stroke-width="1.2" />
        <circle cx="128" cy="8" r="9.5" fill="#8CD156" stroke="#7A5A2F" stroke-width="2" />
        <circle cx="128" cy="8" r="3.8" fill="#EAF7D5" />
      </g>
    </g>

    <!-- 5. 手前の明るい坂道（愛犬3兄弟がトコトコ走るパトロールの小道） -->
    <path d="M -20 460 Q 280 415 580 462 Q 790 495 980 475 L 980 760 L -20 760 Z" fill="#7DA856" />
    <!-- 坂道の小道 -->
    <path d="M -20 535 Q 290 450 620 500 Q 820 530 980 520 L 980 760 L -20 760 Z" fill="url(#roadGrad)" stroke="#C9AE84" stroke-width="2" />

    <!-- 道端の野花・クローバー・落ちたみかん -->
    <g>
      <circle cx="88" cy="500" r="12" fill="#F58220" stroke="#59351C" stroke-width="1.4" />
      <path d="M 88 488 Q 94 482 99 486 Q 93 491 88 488 Z" fill="#4D8741" />
      <circle cx="114" cy="508" r="10.5" fill="#FF9838" stroke="#59351C" stroke-width="1.3" />
      <circle cx="865" cy="505" r="11.5" fill="#F58220" stroke="#59351C" stroke-width="1.4" />
      <!-- 足跡（肉球）ステップ -->
      <g fill="#B8996E" opacity="0.65">
        <circle cx="145" cy="645" r="6" /><circle cx="138" cy="636" r="2.6" /><circle cx="145" cy="634" r="2.6" /><circle cx="152" cy="636" r="2.6" />
        <circle cx="190" cy="628" r="6" /><circle cx="183" cy="619" r="2.6" /><circle cx="190" cy="617" r="2.6" /><circle cx="197" cy="619" r="2.6" />
      </g>
    </g>

    <!-- ================================================================= -->
    <!-- 6. 愛犬3兄弟パトロール隊（左：長男みろく / 中：次男JIN / 右：三男ハク） -->
    <!-- ================================================================= -->

    <!-- 【長男：みろく】赤いハーネス＋紫リードを着けた笑顔の大きめ豆柴（パトロール隊長） -->
    <g id="dog-miroku" transform="translate(278, 548)" filter="url(#cardShadow)">
      <!-- 影 -->
      <ellipse cx="5" cy="112" rx="74" ry="16" fill="rgba(60, 42, 24, 0.18)" />
      <!-- くるんと巻いた柴犬の尻尾 -->
      <path d="M -56 20 C -92 -5, -88 -48, -54 -44 C -36 -42, -36 -12, -48 12 Z" fill="#D58946" stroke="#4A2E1B" stroke-width="2.6" />
      <path d="M -68 -18 C -76 -32, -62 -40, -50 -30" fill="none" stroke="#FAF2E4" stroke-width="5" stroke-linecap="round" />
      <!-- 後ろ足 -->
      <path d="M -48 45 L -56 104 C -56 110, -40 110, -38 104 L -30 52 Z" fill="#D58946" stroke="#4A2E1B" stroke-width="2.5" />
      <ellipse cx="-47" cy="106" rx="11" ry="6" fill="#FAF2E4" stroke="#4A2E1B" stroke-width="2.2" />
      <!-- 胴体 -->
      <ellipse cx="-8" cy="36" rx="58" ry="38" fill="#D88C4A" stroke="#4A2E1B" stroke-width="2.6" />
      <!-- 胸元の白い毛（裏白） -->
      <path d="M 16 8 C 46 14, 52 54, 18 68 C -2 68, -4 34, 16 8 Z" fill="#FCF6EA" />
      <!-- トレードマークの赤いハーネス（Fuzzyard風）と紫のリードストラップ -->
      <path d="M -22 2 C 2 0, 26 8, 36 30 C 30 56, 8 68, -16 64 C -10 42, -14 20, -22 2 Z" fill="#C82829" stroke="#4A1212" stroke-width="2.4" />
      <rect x="4" y="30" width="22" height="12" rx="3" fill="#232B32" stroke="#FAF2E4" stroke-width="1" />
      <!-- 金具と紫のリード -->
      <circle cx="-8" cy="10" r="5" fill="#E2E6E8" stroke="#444" stroke-width="1.8" />
      <path d="M -8 12 Q -55 45 -115 72" fill="none" stroke="#5A287D" stroke-width="6.5" stroke-linecap="round" />
      <!-- 前足（元気に歩くポーズ） -->
      <path d="M 10 56 L 4 108 C 4 114, 20 114, 22 108 L 26 56 Z" fill="#D88C4A" stroke="#4A2E1B" stroke-width="2.5" />
      <ellipse cx="13" cy="110" rx="11" ry="6" fill="#FAF2E4" stroke="#4A2E1B" stroke-width="2.2" />
      <path d="M 32 52 L 48 98 C 50 104, 64 100, 60 92 L 44 46 Z" fill="#D88C4A" stroke="#4A2E1B" stroke-width="2.5" />
      <ellipse cx="55" cy="98" rx="11" ry="6" fill="#FAF2E4" stroke="#4A2E1B" stroke-width="2.2" />

      <!-- みろくの顔（ピンと立った三角耳と満面の笑顔、長いピンクの舌） -->
      <g transform="translate(22, -32)">
        <!-- 左耳・右耳 -->
        <polygon points="-28,-16 -36,-64 -4,-34" fill="#C97B3B" stroke="#4A2E1B" stroke-width="2.6" stroke-linejoin="round" />
        <polygon points="-25,-22 -31,-52 -10,-34" fill="#F5DFC6" />
        <polygon points="28,-16 36,-64 4,-34" fill="#C97B3B" stroke="#4A2E1B" stroke-width="2.6" stroke-linejoin="round" />
        <polygon points="25,-22 31,-52 10,-34" fill="#F5DFC6" />
        <!-- 顔の輪郭 -->
        <ellipse cx="0" cy="-6" rx="42" ry="37" fill="#DC904D" stroke="#4A2E1B" stroke-width="2.6" />
        <!-- 柴犬ならではの白いほっぺとマズル・麻呂眉 -->
        <path d="M -38 0 C -36 -14, -16 -8, 0 -4 C 16 -8, 36 -14, 38 0 C 36 22, 20 31, 0 31 C -20 31, -36 22, -38 0 Z" fill="#FCF6EA" />
        <ellipse cx="-14" cy="-22" rx="5.5" ry="3.2" fill="#FCF6EA" />
        <ellipse cx="14" cy="-22" rx="5.5" ry="3.2" fill="#FCF6EA" />
        <!-- つぶらな瞳とハイライト -->
        <circle cx="-16" cy="-12" r="5.8" fill="#231815" />
        <circle cx="-18" cy="-14" r="2" fill="#FFFFFF" />
        <circle cx="16" cy="-12" r="5.8" fill="#231815" />
        <circle cx="14" cy="-14" r="2" fill="#FFFFFF" />
        <!-- 黒い鼻 -->
        <ellipse cx="0" cy="-1" rx="7.5" ry="5.5" fill="#231815" />
        <ellipse cx="-2" cy="-2.5" rx="2.2" ry="1.2" fill="#FFFFFF" opacity="0.65" />
        <!-- 大きく開いた笑顔の口とピンクの舌 -->
        <path d="M -19 6 Q 0 22 19 6" fill="#4A1E1B" stroke="#231815" stroke-width="2.4" stroke-linecap="round" />
        <path d="M -9 10 L -9 29 C -9 38, 9 38, 9 29 L 9 10 Z" fill="#F08A99" stroke="#4A1E1B" stroke-width="2" />
        <line x1="0" y1="11" x2="0" y2="29" stroke="#D46A7A" stroke-width="1.4" />
      </g>
    </g>

    <!-- 【次男：JIN】オレンジ色の服を着た垂れ耳チワックス（パトロール副隊長） -->
    <g id="dog-jin" transform="translate(525, 575)" filter="url(#cardShadow)">
      <!-- 影 -->
      <ellipse cx="0" cy="92" rx="68" ry="14" fill="rgba(60, 42, 24, 0.18)" />
      <!-- ふさふさの飾り毛がついた尻尾 -->
      <path d="M -52 10 C -78 -14, -75 -46, -48 -52 C -38 -34, -36 -10, -44 14 Z" fill="#C98243" stroke="#4A2E1B" stroke-width="2.4" />
      <!-- 胴長短足の愛らしいダックス体型＋オレンジ色・緑パイピングの服 -->
      <ellipse cx="-6" cy="34" rx="54" ry="29" fill="#C98243" stroke="#4A2E1B" stroke-width="2.5" />
      <!-- オレンジのベスト（緑の縁取り付き） -->
      <path d="M -38 12 C -12 4, 18 6, 32 20 C 34 44, 18 60, -34 56 C -42 42, -42 24, -38 12 Z" fill="#F26924" stroke="#489E44" stroke-width="3" />
      <!-- 短くて可愛い足 -->
      <path d="M -40 52 L -44 86 C -44 91, -30 91, -28 86 L -24 54 Z" fill="#C98243" stroke="#4A2E1B" stroke-width="2.4" />
      <path d="M 14 52 L 12 88 C 12 93, 26 93, 28 88 L 28 52 Z" fill="#C98243" stroke="#4A2E1B" stroke-width="2.4" />
      <path d="M 30 48 L 46 82 C 48 87, 60 83, 56 76 L 40 44 Z" fill="#C98243" stroke="#4A2E1B" stroke-width="2.4" />

      <!-- JINの顔（ふわふわの大きな垂れ耳・くりっとした大きな瞳・笑顔の舌） -->
      <g transform="translate(26, -12)">
        <!-- ふわふわの垂れ耳（左右） -->
        <path d="M -22 -24 C -48 -26, -58 6, -40 22 C -28 24, -20 8, -22 -24 Z" fill="#A6612A" stroke="#4A2E1B" stroke-width="2.4" />
        <path d="M 22 -24 C 48 -26, 56 6, 38 22 C 26 24, 20 8, 22 -24 Z" fill="#A6612A" stroke="#4A2E1B" stroke-width="2.4" />
        <!-- 頭とマズル -->
        <ellipse cx="0" cy="-6" rx="33" ry="29" fill="#D48C4B" stroke="#4A2E1B" stroke-width="2.5" />
        <ellipse cx="2" cy="6" rx="18" ry="14" fill="#F5E3C8" />
        <!-- チワックスならではの大きく潤んだ黒目 -->
        <circle cx="-13" cy="-9" r="6.8" fill="#1E1410" />
        <circle cx="-15" cy="-11.5" r="2.4" fill="#FFFFFF" />
        <circle cx="15" cy="-9" r="6.8" fill="#1E1410" />
        <circle cx="13" cy="-11.5" r="2.4" fill="#FFFFFF" />
        <!-- 鼻と笑顔の舌 -->
        <ellipse cx="2" cy="1" rx="6.8" ry="5" fill="#1E1410" />
        <path d="M -11 9 Q 2 19 15 9" fill="none" stroke="#3D2218" stroke-width="2.2" stroke-linecap="round" />
        <path d="M -4 13 L -2 27 C -1 33, 12 32, 11 25 L 8 12 Z" fill="#F08A99" stroke="#4A1E1B" stroke-width="1.8" />
      </g>
    </g>

    <!-- 【三男：ハク】ふわふわの真っ白な末っ子子犬（新人パトロール隊員） -->
    <g id="dog-haku" transform="translate(748, 592)" filter="url(#cardShadow)">
      <!-- 影 -->
      <ellipse cx="0" cy="76" rx="52" ry="12" fill="rgba(60, 42, 24, 0.16)" />
      <!-- 元気に振る白い尻尾＋動き線 -->
      <path d="M 36 8 C 56 -6, 58 -30, 40 -34 C 30 -24, 28 -6, 32 12 Z" fill="#FFFDF9" stroke="#6E5A48" stroke-width="2.2" />
      <path d="M 58 -24 Q 66 -14 62 -2" fill="none" stroke="#D96326" stroke-width="2" stroke-linecap="round" />
      <path d="M 65 -18 Q 72 -10 68 0" fill="none" stroke="#D96326" stroke-width="1.6" stroke-linecap="round" />
      <!-- 小さな白い胴体（お兄ちゃんたちの方を向いてトコトコ走る） -->
      <ellipse cx="6" cy="28" rx="38" ry="25" fill="#FFFDF9" stroke="#6E5A48" stroke-width="2.2" />
      <!-- 後ろ足・前足 -->
      <path d="M 24 44 L 30 72 C 30 76, 18 76, 16 72 L 12 46 Z" fill="#FFFDF9" stroke="#6E5A48" stroke-width="2.2" />
      <path d="M -8 44 L -10 74 C -10 78, -22 78, -22 74 L -18 44 Z" fill="#FFFDF9" stroke="#6E5A48" stroke-width="2.2" />
      <path d="M -22 40 L -38 66 C -42 70, -50 64, -46 58 L -30 34 Z" fill="#FFFDF9" stroke="#6E5A48" stroke-width="2.2" />
      <!-- 若草色のバンダナ首輪 -->
      <path d="M -24 14 Q -8 24 6 14 L -10 30 Z" fill="#5D9648" stroke="#3D662E" stroke-width="1.6" />

      <!-- ハクの顔（少し首をかしげた愛らしい白い子犬・クリーム色の耳先） -->
      <g transform="translate(-18, -12) rotate(-6)">
        <!-- ふわっと垂れた子犬の耳 -->
        <path d="M -20 -18 C -38 -22, -44 0, -30 12 C -22 12, -16 0, -20 -18 Z" fill="#F5E4CC" stroke="#6E5A48" stroke-width="2.1" />
        <path d="M 20 -18 C 38 -22, 44 0, 30 12 C 22 12, 16 0, 20 -18 Z" fill="#F5E4CC" stroke="#6E5A48" stroke-width="2.1" />
        <!-- まんまるの白い顔 -->
        <ellipse cx="0" cy="-4" rx="28" ry="25" fill="#FFFDF9" stroke="#6E5A48" stroke-width="2.2" />
        <!-- くりくりの瞳 -->
        <circle cx="-10" cy="-6" r="5.2" fill="#2B1D16" />
        <circle cx="-11.5" cy="-8" r="1.8" fill="#FFFFFF" />
        <circle cx="10" cy="-6" r="5.2" fill="#2B1D16" />
        <circle cx="8.5" cy="-8" r="1.8" fill="#FFFFFF" />
        <!-- 小さな黒い鼻と微笑み -->
        <ellipse cx="0" cy="2" rx="5.2" ry="3.8" fill="#2B1D16" />
        <path d="M -7 8 Q 0 14 7 8" fill="none" stroke="#5A4231" stroke-width="2" stroke-linecap="round" />
        <ellipse cx="-15" cy="2" rx="4.5" ry="2.5" fill="#F7B7B2" opacity="0.65" />
        <ellipse cx="15" cy="2" rx="4.5" ry="2.5" fill="#F7B7B2" opacity="0.65" />
      </g>
    </g>

    <!-- 3兄弟の名前ミニラベル（イラスト内の上品な手描き風タグ） -->
    <g font-family='"Noto Sans JP", sans-serif' font-weight="700" font-size="15">
      <g transform="translate(278, 700)">
        <rect x="-78" y="-22" width="156" height="30" rx="15" fill="rgba(252,251,247,0.92)" stroke="#D96326" stroke-width="1.5" />
        <text x="0" y="-1" text-anchor="middle" fill="#142330">隊長 みろく（豆柴）</text>
      </g>
      <g transform="translate(525, 700)">
        <rect x="-102" y="-22" width="204" height="30" rx="15" fill="rgba(252,251,247,0.94)" stroke="#4A6B5D" stroke-width="1.5" />
        <text x="0" y="-1" text-anchor="middle" fill="#142330">副隊長 JIN（チワックス）</text>
      </g>
      <g transform="translate(748, 700)">
        <rect x="-76" y="-22" width="152" height="30" rx="15" fill="rgba(252,251,247,0.92)" stroke="#3A6E88" stroke-width="1.5" />
        <text x="0" y="-1" text-anchor="middle" fill="#142330">新人 ハク（白い犬）</text>
      </g>
    </g>

    <!-- 7. カバーイラスト上部のエディトリアル・タイポグラフィバッジ -->
    <g transform="translate(36, 32)">
      <rect x="0" y="0" width="530" height="84" rx="16" fill="rgba(252, 251, 247, 0.88)" stroke="rgba(20, 35, 48, 0.16)" stroke-width="1.5" />
      <text x="22" y="30" fill="#D96326" font-size="14" font-weight="700" letter-spacing="0.14em">DAILY FIELD DISPATCH ― SETOUCHI &amp; UWA SEA</text>
      <text x="22" y="61" fill="#142330" font-size="22" font-weight="700" letter-spacing="0.04em">愛媛の空き家パトロール隊、今日も行く！</text>
    </g>
  </svg>
</div>
</body>
</html>
"""


def main():
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page(viewport={"width": 960, "height": 760}, device_scale_factor=2)
        page.set_content(ILLUSTRATION_HTML, wait_until="networkidle")
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT_PNG), full_page=False)
        browser.close()
    print(f"Generated {OUT_PNG} ({OUT_PNG.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
