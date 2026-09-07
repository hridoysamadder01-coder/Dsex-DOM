/*
 * Frontend runtime config.
 *
 * WS_URL resolution order (first non-empty wins):
 *   1. ?ws=...            query-string override   (e.g. ?ws=wss://api.example.com/dom)
 *   2. window.DOM_CONFIG.WS_URL   (set below -- edit this for production)
 *   3. same-origin        ws(s)://<this-host>/dom (when the backend serves this page)
 *   4. ws://127.0.0.1:8000/dom    local dev fallback
 *
 * DEV  (one-shot): leave WS_URL null, open http://127.0.0.1:8000/ -> auto-detected.
 * PROD (Hostinger frontend + Render backend): set WS_URL to your wss:// endpoint,
 *      e.g. "wss://dsex-dom.onrender.com/dom".
 */
window.DOM_CONFIG = {
  WS_URL: null,
};
