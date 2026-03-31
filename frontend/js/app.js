/**
 * app.js — ISBN Scanner
 *
 * Scan tab:  one-tap start, camera stays open, auto-submits each barcode,
 *            3-second cooldown between scans, result shown inline.
 * Boxes tab: list of boxes → click to see contents.
 */

const API_BASE = window.API_BASE || "";
const COOLDOWN_MS    = 3000;
const POLL_INTERVAL  = 2000;
const MAX_POLLS      = 30;

const $ = (sel, ctx = document) => ctx.querySelector(sel);
const show = el => el.classList.remove("hidden");
const hide = el => el.classList.add("hidden");
const esc  = s  => (s == null ? "" : String(s)
  .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"));

async function apiFetch(path, opts = {}) {
  const res  = await fetch(API_BASE + path, { headers:{"Content-Type":"application/json"}, ...opts });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

// ─────────────────────────────────────────────────────────────────────────────
// Tab switching
// ─────────────────────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`pane-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "browse") loadBoxList();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Scan tab — continuous scanning
// ─────────────────────────────────────────────────────────────────────────────
const videoEl      = $("#viewfinder");
const viewfinderW  = $("#viewfinder-wrap");
const scanFlash    = $("#scan-flash");
const btnStart     = $("#btn-start-scan");
const btnStop      = $("#btn-stop-scan");
const btnOcr       = $("#btn-ocr");
const ocrOverlay   = $("#ocr-capture-overlay");
const ocrStatusMsg = $("#ocr-status-msg");
const statusHint   = $("#scanner-status");
const cooldownBar  = $("#cooldown-bar");
const lastScanSec  = $("#last-scan-section");
const lastScanCard = $("#last-scan-card");

// ─── Scan feedback: sound + flash ────────────────────────────────────────────
let _audioCtx = null;
function _getAudioCtx() {
  if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  return _audioCtx;
}

function playScanBeep() {
  try {
    const ctx  = _getAudioCtx();
    const osc  = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    osc.frequency.value = 1200;
    gain.gain.setValueAtTime(0.25, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.12);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.12);
  } catch (_) {}
}

function triggerScanFlash() {
  scanFlash.classList.add("flashing");
  setTimeout(() => scanFlash.classList.remove("flashing"), 120);
}

let lastBarcode   = null;
let cooldownTimer = null;
let inCooldown    = false;

btnStart.addEventListener("click", async () => {
  statusHint.textContent = "Requesting camera…";
  btnStart.disabled = true;
  show(viewfinderW);

  await startScanner(
    videoEl,
    (barcode) => {
      // Ignore during cooldown or if same barcode repeated
      if (inCooldown || barcode === lastBarcode) return;
      lastBarcode = barcode;
      inCooldown  = true;
      playScanBeep();
      triggerScanFlash();
      startCooldownBar();
      submitBarcode(barcode);
      cooldownTimer = setTimeout(() => {
        inCooldown  = false;
        lastBarcode = null;
        statusHint.textContent = "Point camera at next barcode…";
        cooldownBar.style.transition = "none";
        cooldownBar.style.width = "0%";
      }, COOLDOWN_MS);
    },
    (err) => {
      statusHint.textContent = `Camera error: ${err.message || err}`;
      btnStart.disabled = false;
      hide(viewfinderW);
      hide(btnStop);
      show(btnStart);
    }
  );

  hide(btnStart);
  show(btnStop);
  show(btnOcr);
  preloadOCR();   // warm up worker in background while user scans normally
  statusHint.textContent = "Point camera at a barcode…";
});

btnStop.addEventListener("click", () => {
  stopScanner();
  clearTimeout(cooldownTimer);
  inCooldown  = false;
  lastBarcode = null;
  hide(viewfinderW);
  hide(btnStop);
  hide(btnOcr);
  show(btnStart);
  btnStart.disabled = false;
  statusHint.textContent = "";
  cooldownBar.style.width = "0%";
});

// ─── OCR button ─────────────────────────────────────────────────────────────
let _ocrBusy = false;

btnOcr.addEventListener("click", async () => {
  if (_ocrBusy) return;
  _ocrBusy = true;
  btnOcr.disabled = true;

  // Show the overlay so the user knows a frame was captured
  show(ocrOverlay);
  ocrStatusMsg.textContent = "Captured — loading OCR engine…";

  try {
    await runOCR(
      videoEl,
      // Found an ISBN
      (isbn) => {
        hide(ocrOverlay);
        statusHint.textContent = `📖 OCR found: ${isbn}`;
        submitBarcode(isbn);
      },
      // No ISBN in extracted text
      (rawText) => {
        hide(ocrOverlay);
        if (rawText) {
          // Fill the manual field so user can correct it
          const field = $("#field-barcode");
          field.value = rawText.split(/\s+/)
            .find(t => /^\d{9,13}[xX]?$/.test(t)) || "";
          statusHint.textContent = "OCR: no ISBN found — check manual field";
        } else {
          statusHint.textContent = "OCR: no text detected — try again";
        }
      },
      // Progress messages
      (msg) => { ocrStatusMsg.textContent = msg; }
    );
  } catch (err) {
    hide(ocrOverlay);
    statusHint.textContent = `OCR error: ${err.message || err}`;
  } finally {
    _ocrBusy = false;
    btnOcr.disabled = false;
  }
});

function startCooldownBar() {
  cooldownBar.style.transition = "none";
  cooldownBar.style.width = "100%";
  requestAnimationFrame(() => {
    cooldownBar.style.transition = `width ${COOLDOWN_MS}ms linear`;
    cooldownBar.style.width = "0%";
  });
}

// Manual form submit (typed barcode)
$("#scan-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const barcode = $("#field-barcode").value.trim();
  if (!barcode) return;
  submitBarcode(barcode);
  $("#field-barcode").value = "";
});

async function submitBarcode(barcode) {
  const boxNumber = $("#field-box").value.trim();
  const location  = $("#field-location").value.trim();
  const notes     = $("#field-notes").value.trim();

  if (location) addLocationSuggestion(location);

  statusHint.textContent = `✔ ${barcode} — looking up…`;
  show(lastScanSec);
  lastScanCard.innerHTML = '<div class="spinner"></div>';

  const { ok, data } = await apiFetch("/scan", {
    method: "POST",
    body: JSON.stringify({ barcode, box_number: boxNumber || null, location: location || null, notes: notes || null }),
  });

  if (!ok) {
    lastScanCard.innerHTML = `<div class="result-not-found"><p class="result-title">Error</p>
      <p class="result-meta">${esc(data.error || "Submission failed")}</p></div>`;
    return;
  }

  pollScan(data.scan_id, 0, barcode);
}

function pollScan(scanId, attempts, barcode) {
  if (attempts >= MAX_POLLS) {
    lastScanCard.innerHTML = `<div class="result-not-found">
      <p class="result-title">Timed out</p>
      <p class="result-meta">Barcode: ${esc(barcode)}</p></div>`;
    return;
  }
  setTimeout(async () => {
    const { ok, data } = await apiFetch(`/scan/${scanId}`);
    if (!ok || data.status === "pending") { pollScan(scanId, attempts + 1, barcode); return; }
    renderLastScan(data);
  }, POLL_INTERVAL);
}

function renderLastScan(scan) {
  const item = scan.item;
  if (scan.status === "found" && item) {
    const typeEmoji = { book: "📚", dvd: "🎬", cd: "🎵", other: "📦" }[item.type] || "📦";
    const cover    = item.cover_url
      ? `<img class="result-cover" src="${esc(item.cover_url)}" alt="" loading="lazy" />`
      : `<div class="result-cover-placeholder">${typeEmoji}</div>`;

    let subtitle = "";
    if (item.type === "book")  subtitle = esc((item.authors || []).join(", "));
    if (item.type === "dvd")   subtitle = esc([item.director, item.release_year, item.media_format].filter(Boolean).join(" · "));
    if (item.type === "cd")    subtitle = esc([item.artist, item.label].filter(Boolean).join(" · "));
    if (item.type === "other") subtitle = esc([item.brand, item.category].filter(Boolean).join(" · "));

    lastScanCard.innerHTML = `
      <div class="result-found last-scan-inner">
        ${cover}
        <div>
          <p class="result-title">${esc(item.title)}</p>
          <p class="result-subtitle">${subtitle}</p>
          ${scan.box_number ? `<p class="result-meta">Box: ${esc(scan.box_number)}</p>` : ""}
        </div>
      </div>`;
  } else if (scan.status === "not_found") {
    lastScanCard.innerHTML = `<div class="result-not-found last-scan-inner">
      <p class="result-title">Not found</p>
      <p class="result-meta">Barcode: ${esc(scan.barcode)} — saved but no details found.</p></div>`;
  } else {
    lastScanCard.innerHTML = `<div class="result-not-found last-scan-inner">
      <p class="result-title">Error</p>
      <p class="result-meta">${esc(scan.error_msg || "Lookup failed")}</p></div>`;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Location datalist (localStorage)
// ─────────────────────────────────────────────────────────────────────────────
const LOCATIONS_KEY = "isbn_scanner_locations";

function addLocationSuggestion(loc) {
  const known = getKnownLocations();
  if (!known.includes(loc)) {
    known.push(loc);
    localStorage.setItem(LOCATIONS_KEY, JSON.stringify(known));
    renderLocationDatalist(known);
  }
}
function getKnownLocations() {
  try { return JSON.parse(localStorage.getItem(LOCATIONS_KEY)) || []; } catch { return []; }
}
function renderLocationDatalist(locs) {
  $("#locations-list").innerHTML = locs.map(l => `<option value="${esc(l)}">`).join("");
}
renderLocationDatalist(getKnownLocations());

// ─────────────────────────────────────────────────────────────────────────────
// Boxes tab
// ─────────────────────────────────────────────────────────────────────────────
const browseResults = $("#browse-results");
const browseTitle   = $("#browse-title");
const btnBack       = $("#btn-back-boxes");
const btnPrev       = $("#btn-prev-page");
const btnNext       = $("#btn-next-page");
const pageInd       = $("#page-indicator");
let   browsePage    = 1;
let   browseBox     = null;
let   browseLocation = null;

async function loadBoxList() {
  browseBox = null;
  browsePage = 1;
  browseTitle.textContent = "Boxes";
  hide(btnBack);
  hide(btnPrev);
  hide(btnNext);
  pageInd.textContent = "";
  browseResults.innerHTML = '<p class="hint">Loading…</p>';

  const { ok, data } = await apiFetch("/boxes");
  if (!ok) { browseResults.innerHTML = `<p class="hint">Error: ${esc(data.error)}</p>`; return; }

  const boxes = data.boxes || [];
  if (!boxes.length) { browseResults.innerHTML = '<p class="hint">No boxes yet — start scanning!</p>'; return; }

  browseResults.innerHTML = boxes.map(renderBoxCard).join("");
}

function renderBoxCard(box) {
  const loc = box.location ? `<span class="box-loc">${esc(box.location)}</span>` : "";
  return `<div class="box-card" data-box="${esc(box.box_number)}" data-location="${esc(box.location)}">
    <div class="box-icon">📦</div>
    <div class="box-info">
      <div class="box-number">${esc(box.box_number)}</div>
      <div class="box-meta">${box.item_count} item${box.item_count !== 1 ? "s" : ""}${box.location ? " · " + esc(box.location) : ""}</div>
    </div>
    <div class="box-chevron">›</div>
  </div>`;
}

browseResults.addEventListener("click", (e) => {
  const card = e.target.closest(".box-card");
  if (!card) return;
  browseBox      = card.dataset.box;
  browseLocation = card.dataset.location;
  browsePage     = 1;
  loadBoxItems();
});

btnBack.addEventListener("click", loadBoxList);
btnPrev.addEventListener("click", () => { if (browsePage > 1) { browsePage--; loadBoxItems(); } });
btnNext.addEventListener("click", () => { browsePage++; loadBoxItems(); });

async function loadBoxItems() {
  browseTitle.textContent = `Box: ${browseBox}`;
  show(btnBack);
  browseResults.innerHTML = '<p class="hint">Loading…</p>';
  hide(btnPrev); hide(btnNext); pageInd.textContent = "";

  const params = new URLSearchParams({ box: browseBox, page: browsePage, page_size: 20 });
  const { ok, data } = await apiFetch(`/items?${params}`);

  if (!ok) { browseResults.innerHTML = `<p class="hint">Error: ${esc(data.error)}</p>`; return; }

  if (!data.items?.length) {
    browseResults.innerHTML = '<p class="hint">No items in this box.</p>';
    return;
  }

  browseResults.innerHTML = data.items.map(renderItemCard).join("");
  browsePage <= 1 ? hide(btnPrev) : show(btnPrev);
  data.page < data.total_pages ? show(btnNext) : hide(btnNext);
  pageInd.textContent = `${data.total} item${data.total !== 1 ? "s" : ""}`;
}

function renderItemCard(scan) {
  const item   = scan.item || {};
  const type   = scan.media_type || "other";
  const emojis = { book: "📚", dvd: "🎬", cd: "🎵", other: "📦" };
  const labels = { book: "Book", dvd: "DVD", cd: "CD", other: "Other" };
  const emoji  = emojis[type] || "📦";
  const badge  = `<span class="browse-card-badge badge-${type}">${labels[type] || type}</span>`;
  const statusBadge = scan.status !== "found"
    ? `<span class="browse-card-badge badge-error">${esc(scan.status)}</span>` : "";

  const img = item.cover_url
    ? `<img src="${esc(item.cover_url)}" alt="" loading="lazy" />`
    : `<div class="browse-card-placeholder">${emoji}</div>`;

  const title = esc(item.title || scan.barcode);
  let sub = "";
  if (type === "book") sub = esc((item.authors || []).join(", "));
  if (type === "dvd")  sub = esc([item.director, item.media_format].filter(Boolean).join(" · "));
  if (type === "cd")   sub = esc([item.artist, item.label].filter(Boolean).join(" · "));
  if (type === "other") sub = esc([item.brand, item.category].filter(Boolean).join(" · "));

  return `<div class="browse-card">
    ${img}
    <div>
      <div class="browse-card-title">${badge}${statusBadge}${title}</div>
      <div class="browse-card-meta">${sub}</div>
    </div>
  </div>`;
}
