/**
 * ocr.js — Tesseract.js OCR for ISBN extraction.
 *
 * Exports:
 *   runOCR(videoEl, onFound, onNotFound, onProgress)
 *   preloadOCR()   — optional: warm up the worker early
 */

let _worker = null;
let _loading = false;
let _loadingWaiters = [];

/**
 * Lazily initialise the Tesseract worker (downloads ~10 MB on first call).
 * Subsequent calls return the cached worker instantly.
 */
async function _getWorker() {
  if (_worker) return _worker;

  // If already loading, wait for it rather than spawning a second worker
  if (_loading) {
    return new Promise((resolve) => _loadingWaiters.push(resolve));
  }

  _loading = true;
  try {
    _worker = await Tesseract.createWorker("eng");
    _loadingWaiters.forEach(r => r(_worker));
    _loadingWaiters = [];
    return _worker;
  } finally {
    _loading = false;
  }
}

/** Call this when the scanner starts to warm up the worker in the background. */
function preloadOCR() {
  _getWorker().catch(() => {});
}

/**
 * Capture one frame from the video element and return a canvas.
 */
function _captureFrame(videoEl) {
  const w = videoEl.videoWidth  || 1280;
  const h = videoEl.videoHeight || 720;
  const canvas = document.createElement("canvas");
  canvas.width  = w;
  canvas.height = h;
  canvas.getContext("2d").drawImage(videoEl, 0, 0, w, h);
  return canvas;
}

/**
 * Search OCR text for an ISBN-10 or ISBN-13.
 * Also normalises common OCR substitutions (O→0, l/I→1).
 */
function _extractISBN(rawText) {
  // Normalise common OCR character confusions
  const t = rawText
    .replace(/[oO]/g, "0")
    .replace(/\bI\b/g, "1")   // isolated I → 1
    .replace(/\bl\b/g, "1");  // isolated l → 1

  // ISBN-13: 978 or 979 followed by 10 more digits (hyphens/spaces allowed)
  const m13 = t.match(/97[89][\s\-]?(?:\d[\s\-]?){9}\d/);
  if (m13) return m13[0].replace(/[\s\-]/g, "");

  // ISBN-10: 9 digits + digit or X (with optional separators)
  const m10 = rawText.match(/\b(?:\d[\s\-]?){9}[\dXx]\b/);
  if (m10) return m10[0].replace(/[\s\-]/g, "").toUpperCase();

  return null;
}

/**
 * Run OCR on the current video frame.
 *
 * @param {HTMLVideoElement} videoEl
 * @param {function(string)}  onFound     - called with the ISBN string if found
 * @param {function(string)}  onNotFound  - called with raw extracted text if no ISBN found
 * @param {function(string)}  onProgress  - called with status messages during processing
 */
async function runOCR(videoEl, onFound, onNotFound, onProgress) {
  const canvas = _captureFrame(videoEl);

  onProgress("Loading OCR engine…");
  const worker = await _getWorker();

  onProgress("Reading text…");
  const result = await worker.recognize(canvas);
  const text   = result.data.text || "";

  const isbn = _extractISBN(text);
  if (isbn) {
    onFound(isbn);
  } else {
    onNotFound(text.trim());
  }
}
