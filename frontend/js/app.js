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
    if (btn.dataset.tab === "browse")      loadBoxList();
    if (btn.dataset.tab === "unresolved")  loadUnresolved();
  });
});

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
  const tabBtn = document.querySelector(`.tab[data-tab="${tabName}"]`);
  if (tabBtn) tabBtn.classList.add("active");
  const pane = document.getElementById(`pane-${tabName}`);
  if (pane) pane.classList.add("active");
  if (tabName === "unresolved") loadUnresolved();
}

if (window.DEPLOY_TIME) {
  $("#deploy-time").textContent = `Deployed: ${new Date(window.DEPLOY_TIME).toLocaleString()}`;
}

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
const scanToast    = $("#scan-toast");

let _scannerRunning = false;

function _pauseScanner(reason) {
  if (!_scannerRunning) return;
  _scannerRunning = false;
  stopScanner();
  clearTimeout(cooldownTimer);
  inCooldown  = false;
  lastBarcode = null;
  hide(viewfinderW);
  hide(btnStop);
  hide(btnOcr);
  show(btnStart);
  btnStart.disabled = false;
  statusHint.textContent = reason;
  cooldownBar.style.width = "0%";
}

// Stop camera when page is hidden (screen lock, app switch, tab change)
document.addEventListener("visibilitychange", () => {
  if (document.hidden) _pauseScanner("📷 Scanner paused — tap Start to resume.");
});

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

function triggerCaptureFlash() {
  scanFlash.classList.add("capturing");
  setTimeout(() => scanFlash.classList.remove("capturing"), 250);
}

function playCaptureClick() {
  try {
    const ctx  = _getAudioCtx();
    const buf  = ctx.createBuffer(1, ctx.sampleRate * 0.04, ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) {
      data[i] = (Math.random() * 2 - 1) * (1 - i / data.length);
    }
    const src = ctx.createBufferSource();
    src.buffer = buf;
    const gain = ctx.createGain();
    gain.gain.value = 0.3;
    src.connect(gain);
    gain.connect(ctx.destination);
    src.start();
  } catch (_) {}
}

function hideScanToast() {
  if (_toastTimer) { clearTimeout(_toastTimer); _toastTimer = null; }
  scanToast.classList.add("hidden");
  scanToast.classList.remove("toast-show", "toast-hide");
}

function showNoDetectToast() {
  scanToast.innerHTML = `<span class="toast-emoji">📷</span><div class="toast-body"><span class="toast-title">No barcode detected</span><span class="toast-sub">Tap again or hold steady</span></div>`;
  scanToast.classList.remove("hidden", "toast-hide");
  scanToast.classList.add("toast-show");
  _toastTimer = setTimeout(() => {
    scanToast.classList.replace("toast-show", "toast-hide");
    setTimeout(() => scanToast.classList.add("hidden"), 400);
  }, 2000);
}

const MAX_RETRIES = 24;
function _retryHint(retryCount) {
  const count = retryCount ?? 0;
  if (count >= MAX_RETRIES) return `<span class="toast-retry exhausted">Retries exhausted</span>`;
  return `<span class="toast-retry">🔄 Will retry automatically (${count}/${MAX_RETRIES})</span>`;
}
function _retryHintText(retryCount) {
  const count = retryCount ?? 0;
  if (count >= MAX_RETRIES) return "Retries exhausted — reset via database to try again.";
  return `🔄 Will retry automatically (${count}/${MAX_RETRIES} attempts)`;
}
function _retryBadge(status, retryCount) {
  const count = retryCount ?? 0;
  if (count >= MAX_RETRIES)
    return `<span class="browse-card-badge badge-error" title="Retries exhausted">❌ ${esc(status)}</span>`;
  return `<span class="browse-card-badge badge-retry" title="${count}/${MAX_RETRIES} retries used">🔄 ${esc(status)}</span>`;
}

let _toastTimer = null;
function showScanToast(scan) {
  const item = scan.item;
  let html = "";
  if (scan.status === "found" && item) {
    const typeEmoji = { book: "📚", dvd: "🎬", cd: "🎵", other: "📦" }[item.type] || "📦";
    const coverHtml = item.cover_url
      ? `<img class="toast-cover" src="${esc(item.cover_url)}" alt="" />`
      : `<span class="toast-emoji">${typeEmoji}</span>`;
    let sub = "";
    if (item.type === "book")  sub = esc((item.authors || []).slice(0, 1).join(""));
    if (item.type === "dvd")   sub = esc([item.director, item.release_year].filter(Boolean).join(" · "));
    if (item.type === "cd")    sub = esc([item.artist, item.release_year].filter(Boolean).join(" · "));
    if (item.type === "other") sub = esc(item.category || "");
    html = `${coverHtml}<div class="toast-body"><span class="toast-title">${esc(item.title)}</span>${sub ? `<span class="toast-sub">${sub}</span>` : ""}</div>`;
  } else if (scan.status === "not_found") {
    const retryHint = _retryHint(scan.retry_count);
    html = `<span class="toast-emoji">❓</span><div class="toast-body"><span class="toast-title">Not found</span><span class="toast-sub">${esc(scan.barcode)}</span>${retryHint}</div>`;
  } else {
    const retryHint = _retryHint(scan.retry_count);
    html = `<span class="toast-emoji">⚠️</span><div class="toast-body"><span class="toast-title">Lookup failed</span>${retryHint}</div>`;
  }
  scanToast.innerHTML = html;
  scanToast.classList.remove("hidden", "toast-hide");
  scanToast.classList.add("toast-show");
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => {
    scanToast.classList.replace("toast-show", "toast-hide");
    setTimeout(() => scanToast.classList.add("hidden"), 400);
  }, 4000);
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
      _scannerRunning = false;
      statusHint.textContent = `Camera error: ${err.message || err}`;
      btnStart.disabled = false;
      hide(viewfinderW);
      hide(btnStop);
      show(btnStart);
    },
    () => _pauseScanner("📷 Scanner paused after 60s — tap Start to resume.")
  );

  _scannerRunning = true;

  hide(btnStart);
  show(btnStop);
  show(btnOcr);
  preloadOCR();   // warm up worker in background while user scans normally
  statusHint.textContent = "Point camera at a barcode or tap viewfinder…";
});

// Stop camera when page is hidden (screen lock, app switch, tab change)
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (_active ?? false) return; // scanner.js _active not directly accessible; use btnStop visibility
    const scannerOn = btnStop.style.display !== "none" && !btnStop.classList.contains("hidden");
    if (scannerOn) {
      stopScanner();
      clearTimeout(cooldownTimer);
      inCooldown  = false;
      lastBarcode = null;
      hide(viewfinderW);
      hide(btnStop);
      hide(btnOcr);
      show(btnStart);
      btnStart.disabled = false;
      statusHint.textContent = "📷 Scanner paused — tap Start to resume.";
      cooldownBar.style.width = "0%";
    }
  }
});

btnStop.addEventListener("click", () => {
  _pauseScanner("");
});

// Tap viewfinder to manually trigger a decode — fallback for Android
viewfinderW.addEventListener("click", () => {
  if (inCooldown) return;
  // Immediate feedback so the user knows the tap registered
  triggerCaptureFlash();
  playCaptureClick();
  hideScanToast();
  statusHint.textContent = "Scanning…";

  const barcode = scanCurrentFrame(videoEl);
  if (barcode) {
    if (barcode === lastBarcode) return;
    lastBarcode = barcode;
    inCooldown  = true;
    playScanBeep();
    triggerScanFlash();
    startCooldownBar();
    submitBarcode(barcode);
    cooldownTimer = setTimeout(() => {
      inCooldown  = false;
      lastBarcode = null;
      statusHint.textContent = "Point camera at next barcode or tap viewfinder…";
      cooldownBar.style.transition = "none";
      cooldownBar.style.width = "0%";
    }, COOLDOWN_MS);
  } else {
    statusHint.textContent = "Point camera at a barcode or tap viewfinder…";
    showNoDetectToast();
  }
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

// Box field gates the Start Scanner button
const fieldBox = $("#field-box");
function updateStartBtn() {
  btnStart.disabled = fieldBox.value.trim() === "";
}
fieldBox.addEventListener("input", updateStartBtn);

// Manual form submit (typed barcode)
$("#manual-form").addEventListener("submit", (e) => {
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
  showScanToast(scan);
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
      <p class="result-meta">Barcode: ${esc(scan.barcode)} — saved but no details found.</p>
      <p class="result-retry">${_retryHintText(scan.retry_count)}</p></div>`;
  } else {
    lastScanCard.innerHTML = `<div class="result-not-found last-scan-inner">
      <p class="result-title">Error</p>
      <p class="result-meta">${esc(scan.error_msg || "Lookup failed")}</p>
      <p class="result-retry">${_retryHintText(scan.retry_count)}</p></div>`;
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
  browseTitle.textContent = browseLocation ? `Box: ${browseBox} · ${browseLocation}` : `Box: ${browseBox}`;
  show(btnBack);
  browseResults.innerHTML = '<p class="hint">Loading…</p>';
  hide(btnPrev); hide(btnNext); pageInd.textContent = "";

  const params = new URLSearchParams({ box: browseBox, page: browsePage, page_size: 20 });
  if (browseLocation) params.set("location", browseLocation);
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

browseResults.addEventListener("click", async (e) => {
  const resolveBtn = e.target.closest(".btn-browse-resolve");
  if (resolveBtn) {
    openResolveModal(resolveBtn.dataset.scanId, resolveBtn.dataset.barcode);
    return;
  }
  const btn = e.target.closest(".btn-delete-scan");
  if (!btn) return;
  const scanId = btn.dataset.scanId;
  if (!scanId) return;
  if (!confirm("Delete this entry?")) return;
  btn.disabled = true;
  btn.textContent = "⏳";
  const { ok, data } = await apiFetch(`/scan/${encodeURIComponent(scanId)}`, { method: "DELETE" });
  if (ok) {
    btn.closest(".browse-card").remove();
    const remaining = browseResults.querySelectorAll(".browse-card").length;
    if (remaining === 0) {
      browseResults.innerHTML = '<p class="hint">No items in this box.</p>';
      pageInd.textContent = "";
    } else {
      const current = parseInt(pageInd.textContent) || remaining + 1;
      const newTotal = current - 1;
      pageInd.textContent = `${newTotal} item${newTotal !== 1 ? "s" : ""}`;
    }
  } else {
    btn.disabled = false;
    btn.textContent = "🗑";
    alert(`Delete failed: ${data?.error || "unknown error"}`);
  }
});

function renderItemCard(scan) {
  const item   = scan.item || {};
  const type   = scan.media_type || "other";
  const emojis = { book: "📚", dvd: "🎬", cd: "🎵", other: "📦" };
  const labels = { book: "Book", dvd: "DVD", cd: "CD", other: "Other" };
  const emoji  = emojis[type] || "📦";
  const badge  = `<span class="browse-card-badge badge-${type}">${labels[type] || type}</span>`;
  const statusBadge = scan.status !== "found"
    ? _retryBadge(scan.status, scan.retry_count) : "";

  const img = item.cover_url
    ? `<img src="${esc(item.cover_url)}" alt="" loading="lazy" />`
    : `<div class="browse-card-placeholder">${emoji}</div>`;

  const title = esc(item.title || scan.barcode);
  let sub = "";
  if (type === "book") sub = esc((item.authors || []).join(", "));
  if (type === "dvd")  sub = esc([item.director, item.media_format].filter(Boolean).join(" · "));
  if (type === "cd")   sub = esc([item.artist, item.label].filter(Boolean).join(" · "));
  if (type === "other") sub = esc([item.brand, item.category].filter(Boolean).join(" · "));

  const resolveLink = (scan.status === "not_found" || scan.status === "error")
    ? `<button class="btn-browse-resolve" data-scan-id="${esc(scan.scan_id)}" data-barcode="${esc(scan.barcode)}">✏️ Resolve</button>`
    : "";

  return `<div class="browse-card" data-scan-id="${esc(scan.scan_id)}">
    ${img}
    <div class="browse-card-body">
      <div class="browse-card-title">${badge}${statusBadge}${title}</div>
      <div class="browse-card-meta">${sub}</div>
      ${resolveLink}
    </div>
    <button class="btn-delete-scan" title="Delete this entry" aria-label="Delete" data-scan-id="${esc(scan.scan_id)}">🗑</button>
  </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Search tab
// ─────────────────────────────────────────────────────────────────────────────
const searchInput   = $("#search-input");
const searchResults = $("#search-results");
const searchPageInd = $("#search-page-indicator");
const btnSearchPrev = $("#btn-search-prev");
const btnSearchNext = $("#btn-search-next");

let searchPage    = 1;
let searchQuery   = "";
let _searchTimer  = null;

searchInput.addEventListener("input", () => {
  clearTimeout(_searchTimer);
  const q = searchInput.value.trim();
  if (q.length < 2) {
    searchResults.innerHTML = '<p class="hint">Start typing to search your collection.</p>';
    hide(btnSearchPrev); hide(btnSearchNext);
    searchPageInd.textContent = "";
    return;
  }
  _searchTimer = setTimeout(() => {
    searchQuery = q;
    searchPage  = 1;
    runSearch();
  }, 350);
});

btnSearchPrev.addEventListener("click", () => { if (searchPage > 1) { searchPage--; runSearch(); } });
btnSearchNext.addEventListener("click", () => { searchPage++; runSearch(); });

async function runSearch() {
  searchResults.innerHTML = '<p class="hint">Searching…</p>';
  hide(btnSearchPrev); hide(btnSearchNext);
  searchPageInd.textContent = "";

  const params = new URLSearchParams({ q: searchQuery, page: searchPage, page_size: 20 });
  const { ok, data } = await apiFetch(`/search?${params}`);

  if (!ok) {
    searchResults.innerHTML = `<p class="hint">Error: ${esc(data.error || "Search failed")}</p>`;
    return;
  }

  if (!data.items?.length) {
    searchResults.innerHTML = `<p class="hint">No results for "${esc(searchQuery)}".</p>`;
    return;
  }

  searchResults.innerHTML = data.items.map(renderSearchCard).join("");

  const { page, total_pages, total } = data;
  searchPageInd.textContent = `${total} result${total !== 1 ? "s" : ""}${total_pages > 1 ? ` · page ${page}/${total_pages}` : ""}`;
  page > 1        ? show(btnSearchPrev) : hide(btnSearchPrev);
  page < total_pages ? show(btnSearchNext) : hide(btnSearchNext);
}

function renderSearchCard(scan) {
  const item   = scan.item || {};
  const type   = scan.media_type || "other";
  const emojis = { book: "📚", dvd: "🎬", cd: "🎵", other: "📦" };
  const labels = { book: "Book", dvd: "DVD", cd: "CD", other: "Other" };
  const emoji  = emojis[type] || "📦";
  const badge  = `<span class="browse-card-badge badge-${type}">${labels[type] || type}</span>`;

  const img = item.cover_url
    ? `<img src="${esc(item.cover_url)}" alt="" loading="lazy" />`
    : `<div class="browse-card-placeholder">${emoji}</div>`;

  const title = esc(item.title || scan.barcode);
  let sub = "";
  if (type === "book") sub = esc((item.authors || []).join(", "));
  if (type === "dvd")  sub = esc([item.director, item.media_format].filter(Boolean).join(" · "));
  if (type === "cd")   sub = esc([item.artist, item.label].filter(Boolean).join(" · "));
  if (type === "other") sub = esc([item.brand].filter(Boolean).join(" · "));

  const location = [scan.box_number, scan.location].filter(Boolean).join(" · ");

  return `<div class="browse-card">
    ${img}
    <div class="browse-card-body">
      <div class="browse-card-title">${badge}${title}</div>
      <div class="browse-card-meta">${sub}</div>
      ${location ? `<div class="browse-card-location">📦 ${esc(location)}</div>` : ""}
    </div>
  </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Unresolved tab
// ─────────────────────────────────────────────────────────────────────────────
const unresolvedList   = $("#unresolved-list");
const unresolvedHint   = $("#unresolved-hint");
const unresolvedPageInd = $("#unresolved-page-indicator");
const btnUnresPrev     = $("#btn-unresolved-prev");
const btnUnresNext     = $("#btn-unresolved-next");

let unresolvedPage = 1;

async function loadUnresolved() {
  unresolvedList.innerHTML = '<p class="hint">Loading…</p>';
  hide(btnUnresPrev); hide(btnUnresNext);
  unresolvedPageInd.textContent = "";

  const params = new URLSearchParams({ page: unresolvedPage, page_size: 20 });
  const { ok, data } = await apiFetch(`/failed?${params}`);

  if (!ok) {
    unresolvedList.innerHTML = `<p class="hint">Error loading unresolved items.</p>`;
    return;
  }

  const { items, total, page, total_pages } = data;
  unresolvedHint.textContent = total
    ? `${total} item${total !== 1 ? "s" : ""} couldn't be looked up automatically.`
    : "No unresolved items — everything has been found! 🎉";

  if (!items.length) { unresolvedList.innerHTML = ""; return; }

  unresolvedList.innerHTML = items.map(renderUnresolvedCard).join("");

  unresolvedPageInd.textContent = total_pages > 1 ? `page ${page}/${total_pages}` : "";
  page > 1         ? show(btnUnresPrev) : hide(btnUnresPrev);
  page < total_pages ? show(btnUnresNext) : hide(btnUnresNext);
}

btnUnresPrev.addEventListener("click", () => { unresolvedPage--; loadUnresolved(); });
btnUnresNext.addEventListener("click", () => { unresolvedPage++; loadUnresolved(); });

function renderUnresolvedCard(scan) {
  const retries = scan.retry_count || 0;
  const retryText = retries >= 24
    ? `<span class="badge badge-error">Retries exhausted</span>`
    : `<span class="badge badge-retry">🔄 ${retries}/24 retries</span>`;
  const loc = [scan.box_number, scan.location].filter(Boolean).join(" · ");
  const date = scan.scanned_at ? new Date(scan.scanned_at).toLocaleDateString() : "";

  return `<div class="unresolved-card" id="ures-${esc(scan.scan_id)}">
    <div class="unresolved-card-info">
      <span class="unresolved-barcode">${esc(scan.barcode)}</span>
      ${loc ? `<span class="unresolved-loc">📦 ${esc(loc)}</span>` : ""}
      ${date ? `<span class="unresolved-date">${date}</span>` : ""}
      ${retryText}
    </div>
    <button class="btn-resolve" data-scan-id="${esc(scan.scan_id)}" data-barcode="${esc(scan.barcode)}">✏️ Resolve</button>
  </div>`;
}

unresolvedList.addEventListener("click", e => {
  const btn = e.target.closest(".btn-resolve");
  if (btn) openResolveModal(btn.dataset.scanId, btn.dataset.barcode);
});

// Called from browse cards too
function openResolveModal(scanId, barcode) {
  switchTab("unresolved");
  $("#resolve-barcode-label").textContent = barcode;
  const modal = $("#resolve-modal");
  modal.dataset.scanId  = scanId;
  modal.dataset.barcode = barcode;
  $("#resolve-title").value = "";
  $("#resolve-authors").value = "";
  $("#resolve-publisher").value = "";
  $("#resolve-director").value = "";
  $("#resolve-artist").value = "";
  $("#resolve-label").value = "";
  $("#resolve-brand").value = "";
  $("#resolve-category").value = "";
  $("#resolve-year").value = "";
  $("#resolve-cover-url").value = "";
  $("#resolve-error").classList.add("hidden");
  updateResolveFields();
  show($("#resolve-modal"));
}

function updateResolveFields() {
  const type = $("#resolve-type").value;
  $("#resolve-fields-book").classList.toggle("hidden",  type !== "book");
  $("#resolve-fields-dvd").classList.toggle("hidden",   type !== "dvd");
  $("#resolve-fields-cd").classList.toggle("hidden",    type !== "cd");
  $("#resolve-fields-other").classList.toggle("hidden", type !== "other");
}

$("#resolve-type").addEventListener("change", updateResolveFields);

$("#resolve-modal-backdrop").addEventListener("click", closeResolveModal);
$("#btn-resolve-cancel").addEventListener("click",     closeResolveModal);

function closeResolveModal() {
  hide($("#resolve-modal"));
}

$("#btn-resolve-submit").addEventListener("click", async () => {
  const modal = $("#resolve-modal");
  const scanId = modal.dataset.scanId;
  const type   = $("#resolve-type").value;
  const title  = $("#resolve-title").value.trim();
  const errEl  = $("#resolve-error");

  if (!title) {
    errEl.textContent = "Title is required.";
    errEl.classList.remove("hidden");
    return;
  }

  const body = {
    scan_id:    scanId,
    media_type: type,
    title,
    year:       $("#resolve-year").value ? parseInt($("#resolve-year").value) : null,
    cover_url:  $("#resolve-cover-url").value.trim() || null,
  };

  if (type === "book") {
    body.authors   = $("#resolve-authors").value.trim();
    body.publisher = $("#resolve-publisher").value.trim();
  } else if (type === "dvd") {
    body.director    = $("#resolve-director").value.trim();
    body.media_format = $("#resolve-format").value;
  } else if (type === "cd") {
    body.artist = $("#resolve-artist").value.trim();
    body.label  = $("#resolve-label").value.trim();
  } else {
    body.brand    = $("#resolve-brand").value.trim();
    body.category = $("#resolve-category").value.trim();
  }

  $("#btn-resolve-submit").disabled = true;
  const { ok, data } = await apiFetch("/manual", { method: "POST", body: JSON.stringify(body) });
  $("#btn-resolve-submit").disabled = false;

  if (!ok) {
    errEl.textContent = data.error || "Save failed.";
    errEl.classList.remove("hidden");
    return;
  }

  closeResolveModal();
  // Remove all cards with the same barcode and reload totals
  const barcode = modal.dataset.barcode;
  document.querySelectorAll(".unresolved-card").forEach(card => {
    if (card.querySelector(".btn-resolve")?.dataset.barcode === barcode) card.remove();
  });
  loadUnresolved();
});
