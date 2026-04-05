/**
 * scanner.js — ZXing-js barcode scanning via device camera.
 *
 * Exports:
 *   startScanner(videoEl, onDetect, onError)
 *   stopScanner()
 */

let _reader       = null;
let _active       = false;
let _pollInterval = null;
let _canvas       = null;

/**
 * Start the barcode scanner.
 *
 * Uses decodeFromConstraints (reliable on iOS) plus a manual frame-polling
 * fallback (required on Android Chrome, where the continuous decode callback
 * often never fires even when a barcode is visible).
 *
 * @param {HTMLVideoElement} videoEl   - The <video> element to use as viewfinder.
 * @param {function(string)} onDetect  - Called with the decoded barcode string.
 * @param {function(Error)}  onError   - Called on permission denial or fatal error.
 */
async function startScanner(videoEl, onDetect, onError) {
  if (_active) return;

  try {
    _reader = new ZXing.BrowserMultiFormatReader();
    _active = true;

    // Resolution hints improve decode reliability on Android.
    await _reader.decodeFromConstraints(
      { video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } } },
      videoEl,
      (result, err) => {
        if (!_active) return;
        if (result) onDetect(result.getText());
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
        if (result) onDetect(result.getText());
      } catch (_) { /* no barcode in this frame */ }
    }, 300);

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
  if (_pollInterval) {
    clearInterval(_pollInterval);
    _pollInterval = null;
  }
  if (_reader) {
    try { _reader.reset(); } catch (_) {}
    _reader = null;
  }
}
