/**
 * scanner.js — ZXing-js barcode scanning via device camera.
 *
 * Exports:
 *   startScanner(videoEl, onDetect, onError, onInactive)
 *   stopScanner()
 *   scanCurrentFrame(videoEl)
 */

let _reader           = null;
let _active           = false;
let _pollInterval     = null;
let _canvas           = null;
let _inactivityTimer  = null;
let _onInactive       = null;

const POLL_MS       = 300;
const INACTIVITY_MS = 60_000; // auto-pause after 60s with no detection

function _resetInactivityTimer() {
  clearTimeout(_inactivityTimer);
  if (_onInactive) {
    _inactivityTimer = setTimeout(() => {
      if (_active) _onInactive();
    }, INACTIVITY_MS);
  }
}

/**
 * Start the barcode scanner.
 *
 * Uses decodeFromConstraints (reliable on iOS) plus a manual frame-polling
 * fallback (required on Android Chrome, where the continuous decode callback
 * often never fires even when a barcode is visible).
 *
 * @param {HTMLVideoElement} videoEl    - The <video> element to use as viewfinder.
 * @param {function(string)} onDetect  - Called with the decoded barcode string.
 * @param {function(Error)}  onError   - Called on permission denial or fatal error.
 * @param {function()}       onInactive - Called after INACTIVITY_MS with no detection.
 */
async function startScanner(videoEl, onDetect, onError, onInactive) {
  if (_active) return;
  _onInactive = onInactive || null;

  try {
    _reader = new ZXing.BrowserMultiFormatReader();
    _active = true;
    _resetInactivityTimer();

    // Resolution hints improve decode reliability on Android.
    await _reader.decodeFromConstraints(
      { video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } } },
      videoEl,
      (result, err) => {
        if (!_active) return;
        if (result) {
          _resetInactivityTimer();
          onDetect(result.getText());
        }
      }
    );

    // Android Chrome fallback: poll frames manually every 300ms.
    // decodeFromCanvas() throws when no barcode found — that's normal.
    if (!_canvas) _canvas = document.createElement("canvas");
    _pollInterval = setInterval(() => {
      if (!_active || videoEl.readyState < 2 || !videoEl.videoWidth) return;
      _canvas.width  = videoEl.videoWidth;
      _canvas.height = videoEl.videoHeight;
      _canvas.getContext("2d").drawImage(videoEl, 0, 0);
      try {
        const result = _reader.decodeFromCanvas(_canvas);
        if (result) {
          _resetInactivityTimer();
          onDetect(result.getText());
        }
      } catch (_) { /* no barcode in this frame */ }
    }, POLL_MS);

  } catch (err) {
    _active = false;
    onError(err);
  }
}

/**
 * Attempt to decode a barcode from the current video frame.
 * Returns the barcode string, or null if none found.
 * Used for manual tap-to-scan on Android where the continuous loop is unreliable.
 * Uses a fresh reader instance to avoid state conflicts with the running decode loop.
 */
function scanCurrentFrame(videoEl) {
  if (!_active || !videoEl.videoWidth) return null;
  if (!_canvas) _canvas = document.createElement("canvas");
  _canvas.width  = videoEl.videoWidth;
  _canvas.height = videoEl.videoHeight;
  _canvas.getContext("2d").drawImage(videoEl, 0, 0);
  try {
    const tempReader = new ZXing.BrowserMultiFormatReader();
    return tempReader.decodeFromCanvas(_canvas).getText();
  } catch (_) {
    return null;
  }
}

/**
 * Stop the scanner and release the camera.
 */
function stopScanner() {
  _active = false;
  clearTimeout(_inactivityTimer);
  _inactivityTimer = null;
  if (_pollInterval) {
    clearInterval(_pollInterval);
    _pollInterval = null;
  }
  if (_reader) {
    try { _reader.reset(); } catch (_) {}
    _reader = null;
  }
}
