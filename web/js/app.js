// 外部リンク定数（後から個別記事やフォームURLへ差し替え可能）
const EXTERNAL_LINKS = {
  noteWelcomeUrl: "https://note.com/",
  noteMembershipUrl: "https://note.com/",
  xProfileUrl: "https://x.com/",
  // GoogleフォームURLを設定すると自動的にiframe埋め込みに切り替わります
  googleFormEmbedUrl: "",
  contactEmail: "ehime.akiya.patrol@gmail.com",
};

let SITE_DATA = null;
let ACTIVE_REGION = "all";
let SELECTED_MUNI = "今治市";

const prefersReducedMotion = () =>
  window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function formatNumber(n) {
  return Number(n).toLocaleString("ja-JP");
}

function formatMan(val) {
  if (val === null || val === undefined) return "要相談";
  return Number.isInteger(val) ? `${formatNumber(val)}万円` : `${val.toLocaleString("ja-JP")}万円`;
}

// アース＆マリンパレット準拠のヒートマップ配色
function getHeatmapColor(count) {
  if (!count || count <= 0) return "#EAE5D9";
  if (count <= 9) return "#C8DCE3";
  if (count <= 29) return "#7FA8BA";
  if (count <= 79) return "#3A6E88";
  return "#143B54";
}

function isDarkFill(count) {
  return count >= 30;
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function animateCount(el, target, suffix = "", durationMs = 680) {
  if (!el || target === null || target === undefined) return;
  const end = Number(target);
  if (Number.isNaN(end) || prefersReducedMotion()) {
    el.textContent = `${formatNumber(end)}${suffix}`;
    return;
  }
  const start = performance.now();
  const isFloat = !Number.isInteger(end);
  function frame(now) {
    const t = Math.min(1, (now - start) / durationMs);
    const eased = 1 - Math.pow(1 - t, 3);
    const cur = end * eased;
    const display = isFloat ? cur.toFixed(1) : formatNumber(Math.round(cur));
    el.textContent = `${display}${suffix}`;
    if (t < 1) {
      requestAnimationFrame(frame);
    } else {
      el.textContent = `${isFloat ? end.toLocaleString("ja-JP") : formatNumber(end)}${suffix}`;
    }
  }
  requestAnimationFrame(frame);
}

function initScrollReveal() {
  const els = document.querySelectorAll(".reveal");
  if (!els.length) return;
  if (prefersReducedMotion() || !("IntersectionObserver" in window)) {
    els.forEach((el) => el.classList.add("is-visible"));
    return;
  }
  const obs = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          obs.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.08, rootMargin: "0px 0px -30px 0px" }
  );
  els.forEach((el) => obs.observe(el));
}

function applyExternalLinks() {
  const nw = document.getElementById("link-note-welcome");
  const nm = document.getElementById("link-note-membership");
  const xp = document.getElementById("link-x-profile");
  if (nw) nw.href = EXTERNAL_LINKS.noteWelcomeUrl;
  if (nm) nm.href = EXTERNAL_LINKS.noteMembershipUrl;
  if (xp) xp.href = EXTERNAL_LINKS.xProfileUrl;

  if (EXTERNAL_LINKS.googleFormEmbedUrl) {
    const container = document.getElementById("google-form-container");
    if (container) {
      container.innerHTML = `
        <iframe class="form-iframe" src="${escapeHtml(EXTERNAL_LINKS.googleFormEmbedUrl)}" title="現地代理下見・お問い合わせフォーム">読み込み中…</iframe>
        <p style="margin:12px 0 0; font-size:0.84rem; color:#5F6F7C; text-align:center;">
          フォームが開かない場合は <a href="mailto:${escapeHtml(EXTERNAL_LINKS.contactEmail)}" style="text-decoration:underline;">${escapeHtml(EXTERNAL_LINKS.contactEmail)}</a> までご連絡ください。
        </p>
      `;
    }
  }
}

function renderSummary(summary) {
  const leadCnt = document.getElementById("lead-total-count");
  if (leadCnt) leadCnt.textContent = formatNumber(summary.total_count);

  animateCount(document.getElementById("stat-total"), summary.total_count, "件");
  document.getElementById("stat-breakdown").textContent =
    `売買 ${formatNumber(summary.sale_count)}件 / 賃貸 ${formatNumber(summary.rent_count)}件`;

  if (summary.sale_min_man !== null && summary.sale_min_man !== undefined) {
    animateCount(document.getElementById("stat-min-price"), summary.sale_min_man, "万円");
  } else {
    document.getElementById("stat-min-price").textContent = "要相談";
  }
  document.getElementById("stat-median-price").textContent = `中央値 ${formatMan(summary.sale_median_man)}`;
  document.getElementById("stat-covered").textContent =
    `直接取得 ${summary.covered_municipalities}市町（全${summary.municipality_total}市町監視）`;
  document.getElementById("stat-updated").textContent = summary.last_updated || "--";
  const rc = summary.recent_changes_7d;
  if (rc && (rc.new_count || rc.price_down_count || rc.closed_count)) {
    document.getElementById("stat-recent").textContent =
      `直近7日 新着${formatNumber(rc.new_count)} / 値下げ${formatNumber(rc.price_down_count)} / 終了${formatNumber(rc.closed_count)}件`;
  } else {
    document.getElementById("stat-recent").textContent =
      `直近7日新着・改定 ${formatNumber(summary.recent_7days_count)}件`;
  }
}

function renderMap() {
  if (!SITE_DATA) return;
  const mapByName = new Map(SITE_DATA.municipalities.map((m) => [m.name, m]));
  const svgNS = "http://www.w3.org/2000/svg";

  document.querySelectorAll(".muni-group").forEach((group) => {
    const name = group.getAttribute("data-muni");
    const cx = parseFloat(group.getAttribute("data-cx") || "0");
    const cy = parseFloat(group.getAttribute("data-cy") || "0");
    const info = mapByName.get(name);
    const count = info ? info.count : 0;
    const region = info ? info.region : "";

    const shape = group.querySelector(".muni-shape");
    if (shape) {
      shape.setAttribute("fill", getHeatmapColor(count));
    }

    const matchRegion = ACTIVE_REGION === "all" || region === ACTIVE_REGION;
    group.classList.toggle("dimmed", !matchRegion);
    group.classList.toggle("selected", name === SELECTED_MUNI);

    group.querySelectorAll(".dyn-svg-ui").forEach((el) => el.remove());

    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", String(cx));
    label.setAttribute("y", String(cy - 3));
    label.setAttribute("class", `muni-label dyn-svg-ui ${isDarkFill(count) ? "light-text" : ""}`);
    label.textContent = name;
    group.appendChild(label);

    const pillW = count >= 100 ? 38 : count >= 10 ? 32 : 24;
    const rect = document.createElementNS(svgNS, "rect");
    rect.setAttribute("x", String(cx - pillW / 2));
    rect.setAttribute("y", String(cy + 3));
    rect.setAttribute("width", String(pillW));
    rect.setAttribute("height", "16");
    rect.setAttribute("rx", "8");
    rect.setAttribute("class", "muni-count-bg dyn-svg-ui");
    group.appendChild(rect);

    const cntText = document.createElementNS(svgNS, "text");
    cntText.setAttribute("x", String(cx));
    cntText.setAttribute("y", String(cy + 14.5));
    cntText.setAttribute("class", "muni-count-pill dyn-svg-ui");
    cntText.textContent = `${count}`;
    group.appendChild(cntText);
  });
}

function renderChips() {
  if (!SITE_DATA) return;
  const wrap = document.getElementById("muni-chips");
  const filtered = SITE_DATA.municipalities.filter(
    (m) => ACTIVE_REGION === "all" || m.region === ACTIVE_REGION
  );
  wrap.innerHTML = filtered
    .map(
      (m) => `
      <button type="button" class="muni-chip ${m.name === SELECTED_MUNI ? "active" : ""}" data-muni="${escapeHtml(m.name)}">
        <span>${escapeHtml(m.name)}</span>
        <span class="chip-cnt">${m.count}</span>
      </button>
    `
    )
    .join("");

  wrap.querySelectorAll(".muni-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectMunicipality(btn.getAttribute("data-muni"), false);
    });
  });
}

function renderDetailPanel() {
  if (!SITE_DATA) return;
  const panel = document.getElementById("detail-panel");
  const m = SITE_DATA.municipalities.find((x) => x.name === SELECTED_MUNI) || SITE_DATA.municipalities[0];
  if (!m) return;

  const stats = m.price_stats || {};
  const priceRangeText =
    stats.min_man !== null && stats.min_man !== undefined
      ? `${formatMan(stats.min_man)} 〜 ${formatMan(stats.max_man)}`
      : "個別ページ参照";
  const medianText =
    stats.median_man !== null && stats.median_man !== undefined
      ? formatMan(stats.median_man)
      : "-";

  const t = m.tags || {};
  const tagBadges = [
    t.under_100 > 0 ? `100万円以下 ${t.under_100}件` : "",
    t.farm > 0 ? `畑・農地付 ${t.farm}件` : "",
    t.sea > 0 ? `海近・島 ${t.sea}件` : "",
    t.parking > 0 ? `駐車場有 ${t.parking}件` : "",
    t.good_cond > 0 ? `改修不要・良好 ${t.good_cond}件` : "",
  ].filter(Boolean);

  const mc = m.recent_changes_7d || { new_count: 0, price_down_count: 0, closed_count: 0 };
  const hasMuniChanges = mc.new_count > 0 || mc.price_down_count > 0 || mc.closed_count > 0;
  const changeCountNote = hasMuniChanges
    ? `<span class="teaser-counts">直近7日：新着${mc.new_count}件・値下げ${mc.price_down_count}件・終了${mc.closed_count}件</span>`
    : `<span class="teaser-counts">直近7日の変動ログ・成約スピード集計中</span>`;

  let propsHtml = "";
  if (m.featured && m.featured.length > 0) {
    propsHtml = `
      <h4 class="featured-heading">代表物件サンプル（最大3件）</h4>
      <div class="prop-list">
        ${m.featured
          .map((p) => {
            const specs = [p.layout, p.built, p.land_area ? `土地:${p.land_area}` : "", p.bldg_area ? `建物:${p.bldg_area}` : ""]
              .filter(Boolean)
              .join(" / ");
            return `
              <article class="prop-item">
                <div class="prop-top">
                  <span class="prop-id">${escapeHtml(p.kind)}・${escapeHtml(p.id)}</span>
                  <span class="prop-price">${escapeHtml(p.price)}</span>
                </div>
                <div class="prop-meta">${escapeHtml(p.district)}${specs ? " ｜ " + escapeHtml(specs) : ""}</div>
                ${p.notes ? `<p class="prop-notes">${escapeHtml(p.notes)}</p>` : ""}
                <a class="prop-link" href="${escapeHtml(p.url)}" target="_blank" rel="noopener noreferrer">公式詳細ページを開く ↗</a>
              </article>
            `;
          })
          .join("")}
      </div>
      <div class="member-teaser-box">
        <div class="member-teaser-head">
          <span class="lock-badge" aria-hidden="true">🔒 MEMBER ONLY</span>
          ${changeCountNote}
        </div>
        <p class="member-teaser-text">
          この市町の最新値下げ履歴・全件差分・オープンな場では書けない『地元民の本音メモ』は、noteメンバーシップ限定で配信しています →
        </p>
        <a class="member-teaser-link" href="${escapeHtml(EXTERNAL_LINKS.noteMembershipUrl)}" target="_blank" rel="noopener noreferrer">
          <span>noteメンバーシップ（月額1,280円）で全件データを見る</span>
          <span aria-hidden="true">↗</span>
        </a>
      </div>
    `;
  } else {
    propsHtml = `
      <div class="notice-box">
        <strong>外部ポータル掲載・または調査対象外ステータス</strong><br />
        ${escapeHtml(m.source_notes || "この自治体は外部空き家ポータルへの委託またはメンテナンス等により、自動集計対象外となっています。下の公式ボタンから直接一覧をご確認ください。")}
      </div>
      <div class="member-teaser-box">
        <div class="member-teaser-head">
          <span class="lock-badge" aria-hidden="true">🔒 MEMBER ONLY</span>
          ${changeCountNote}
        </div>
        <p class="member-teaser-text">
          この市町の最新値下げ履歴・全件差分・オープンな場では書けない『地元民の本音メモ』は、noteメンバーシップ限定で配信しています →
        </p>
        <a class="member-teaser-link" href="${escapeHtml(EXTERNAL_LINKS.noteMembershipUrl)}" target="_blank" rel="noopener noreferrer">
          <span>noteメンバーシップ（月額1,280円）で全件データを見る</span>
          <span aria-hidden="true">↗</span>
        </a>
      </div>
    `;
  }

  panel.classList.add("updating");
  requestAnimationFrame(() => {
    panel.innerHTML = `
      <div class="detail-head">
        <div>
          <span class="detail-region-tag">${escapeHtml(m.region)}エリア</span>
          <h3 class="detail-title">${escapeHtml(m.name)}</h3>
        </div>
        <div class="detail-count-hero">
          <div class="detail-count-num">${formatNumber(m.count)}</div>
          <div class="detail-count-unit">募集中物件（件）</div>
        </div>
      </div>

      <div class="editorial-metrics">
        <div class="e-metric">
          <div class="k">売買価格帯（売買${m.sale_count} / 賃貸${m.rent_count}）</div>
          <div class="v">${escapeHtml(priceRangeText)}</div>
        </div>
        <div class="e-metric">
          <div class="k">売買価格の中央値</div>
          <div class="v">${escapeHtml(medianText)}</div>
        </div>
      </div>

      ${
        tagBadges.length > 0
          ? `<div class="tag-row">${tagBadges.map((b) => `<span class="tag-pill">${escapeHtml(b)}</span>`).join("")}</div>`
          : ""
      }

      ${propsHtml}

      <div class="detail-actions">
        <a class="btn btn-navy" href="${escapeHtml(m.list_url)}" target="_blank" rel="noopener noreferrer">
          <span>${escapeHtml(m.name)}の空き家バンク公式一覧を見る</span>
          <span class="btn-arrow" aria-hidden="true">↗</span>
        </a>
        <button type="button" class="btn btn-mikan" id="btn-consult-area">
          <span>このエリアの「現地代理下見（通常1万円・会員8,000円）」を相談する</span>
          <span class="btn-arrow" aria-hidden="true">→</span>
        </button>
      </div>
    `;

    requestAnimationFrame(() => panel.classList.remove("updating"));

    const consultBtn = document.getElementById("btn-consult-area");
    if (consultBtn) {
      consultBtn.addEventListener("click", () => {
        const hint = document.getElementById("selected-area-hint");
        if (hint) {
          hint.style.display = "block";
          hint.innerHTML = `選択中のエリア：<strong>${escapeHtml(m.name)}（${escapeHtml(m.region)}）</strong> ― 下見をご希望の物件URLを添えてお送りください。`;
        }
        const mailBtn = document.getElementById("mailto-btn");
        if (mailBtn) {
          const subj = encodeURIComponent(`【現地代理下見相談】${m.name}の物件について`);
          mailBtn.href = `mailto:${EXTERNAL_LINKS.contactEmail}?subject=${subj}`;
        }
        document.getElementById("contact-section")?.scrollIntoView({ behavior: "smooth" });
      });
    }
  });
}

function selectMunicipality(name, scrollOnMobile) {
  SELECTED_MUNI = name;
  renderMap();
  renderChips();
  renderDetailPanel();
  if (scrollOnMobile && window.innerWidth < 960) {
    document.getElementById("detail-panel")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function bindEvents() {
  document.querySelectorAll(".muni-group").forEach((group) => {
    group.addEventListener("click", () => {
      const name = group.getAttribute("data-muni");
      if (name) selectMunicipality(name, true);
    });
  });

  document.querySelectorAll(".region-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      ACTIVE_REGION = tab.getAttribute("data-region") || "all";
      document.querySelectorAll(".region-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");

      if (SITE_DATA && ACTIVE_REGION !== "all") {
        const cur = SITE_DATA.municipalities.find((m) => m.name === SELECTED_MUNI);
        if (!cur || cur.region !== ACTIVE_REGION) {
          const first = SITE_DATA.municipalities.find((m) => m.region === ACTIVE_REGION && m.count > 0)
            || SITE_DATA.municipalities.find((m) => m.region === ACTIVE_REGION);
          if (first) SELECTED_MUNI = first.name;
        }
      }
      renderMap();
      renderChips();
      renderDetailPanel();
    });
  });
}

async function init() {
  initScrollReveal();
  applyExternalLinks();
  bindEvents();
  try {
    const res = await fetch("data/site_data.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    SITE_DATA = await res.json();
    renderSummary(SITE_DATA.summary);
    if (!SITE_DATA.municipalities.some((m) => m.name === SELECTED_MUNI)) {
      SELECTED_MUNI = SITE_DATA.municipalities[0]?.name || "今治市";
    }
    renderMap();
    renderChips();
    renderDetailPanel();
  } catch (err) {
    console.error("Failed to load site_data.json:", err);
    document.getElementById("detail-panel").innerHTML =
      `<div class="notice-box">データの読み込みに失敗しました（ローカルファイル直開きではなく、HTTPサーバー経由で開いてください）。</div>`;
  }
}

document.addEventListener("DOMContentLoaded", init);
