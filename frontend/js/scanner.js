/**
 * scanner.js — ZXing-js barcode scanning via device camera.
 *
 * Exports:
 *   startScanner(videoEl, onDetect, onError)
 *   stopScanner()
 */

let _reader = null;
let _active = false;

/**
 * Start the barcode scanner.
 *
 * Uses decodeFromConstraints so the browser shows the camera permission
 * prompt immediately without needing to list devices first.
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

    // facingMode 'environment' = rear camera on phones, falls back to webcam on desktop.
    // Using decodeFromConstraints triggers getUserMedia directly, prompting for permission.
    await _reader.decodeFromConstraints(
      { video: { facingMode: { ideal: "environment" } } },
      videoEl,
      (result, err) => {
        if (!_active) return;
        if (result) {
          onDetect(result.getText());
        }
        // err fires every frame when no barcode is visible — safe to ignore
      }
    );
  } catch (err) {
    _active = false;
    onError(err);
  }
}

/**
 * Stop the scanner and release the camera.
 */
function stopScanner() {
  _active = false;
  if (_reader) {
    try { _reader.reset(); } catch (_) {}
    _reader = null;
  }
}
