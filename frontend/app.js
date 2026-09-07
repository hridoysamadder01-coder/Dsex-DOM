/* ============================================================
   DSEX DOM — WebSocket client + order-book renderer
   Wires directly to the backend at ws://<host>/dom.
   ============================================================ */
(() => {
  "use strict";

  // ── Resolve the WebSocket endpoint ──────────────────────────
  function resolveWsUrl() {
    const q = new URLSearchParams(location.search).get("ws");
    if (q) return q;
    if (window.DOM_CONFIG && window.DOM_CONFIG.WS_URL) return window.DOM_CONFIG.WS_URL;
    if (location.protocol === "http:" || location.protocol === "https:") {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      return `${proto}://${location.host}/dom`;
    }
    return "ws://127.0.0.1:8000/dom"; // opened as file:// — dev fallback
  }
  const WS_URL = resolveWsUrl();

  // ── DOM references ──────────────────────────────────────────
  const el = {
    select:    document.getElementById("symbol-select"),
    instName:  document.getElementById("inst-name"),
    instSector:document.getElementById("inst-sector"),
    dot:       document.getElementById("status-dot"),
    statusText:document.getElementById("status-text"),
    ltp:       document.getElementById("s-ltp"),
    change:    document.getElementById("s-change"),
    open:      document.getElementById("s-open"),
    high:      document.getElementById("s-high"),
    low:       document.getElementById("s-low"),
    volume:    document.getElementById("s-volume"),
    value:     document.getElementById("s-value"),
    trades:    document.getElementById("s-trades"),
    spread:    document.getElementById("s-spread"),
    asks:      document.getElementById("asks"),
    bids:      document.getElementById("bids"),
    mid:       document.getElementById("mid"),
    midLtp:    document.getElementById("mid-ltp"),
    midChange: document.getElementById("mid-change"),
    midSpread: document.getElementById("mid-spread"),
    imbBid:    document.getElementById("imb-bid"),
    imbAsk:    document.getElementById("imb-ask"),
    imbBidLbl: document.getElementById("imb-bid-lbl"),
    imbAskLbl: document.getElementById("imb-ask-lbl"),
    feedInfo:  document.getElementById("feed-info"),
    lastUpdate:document.getElementById("last-update"),
  };

  // ── State ───────────────────────────────────────────────────
  let ws = null;
  let reconnectDelay = 500;
  let currentSymbol = new URLSearchParams(location.search).get("symbol") || null;
  let depth = 10;
  let latest = null;       // last snapshot for currentSymbol
  let dirty = false;       // set on new data, cleared by the rAF loop
  let prevLtp = null;
  let askRows = [];        // top → bottom (highest ask first)
  let bidRows = [];        // top → bottom (best bid first)
  const nfInt = new Intl.NumberFormat("en-IN");

  // ── Formatting helpers ──────────────────────────────────────
  const fmtPrice = (p) => (p == null ? "—" : Number(p).toFixed(2));
  const fmtInt   = (n) => (n == null ? "—" : nfInt.format(Math.round(n)));
  function fmtValue(v) {
    if (v == null) return "—";
    if (v >= 1e7) return "৳ " + (v / 1e7).toFixed(2) + " Cr";
    if (v >= 1e5) return "৳ " + (v / 1e5).toFixed(2) + " L";
    return "৳ " + nfInt.format(Math.round(v));
  }

  // ── Ladder construction (build once, then mutate in place) ──
  function makeRow(side) {
    const row = document.createElement("div");
    row.className = "row " + side;
    row.innerHTML =
      '<div class="qty bid"><span class="bar"></span><span class="qv"></span></div>' +
      '<div class="ord ord-bid"></div>' +
      '<div class="price"></div>' +
      '<div class="ord ord-ask"></div>' +
      '<div class="qty ask"><span class="bar"></span><span class="qv"></span></div>';
    return {
      el: row,
      price: row.querySelector(".price"),
      bidQv: row.querySelector(".qty.bid .qv"),
      bidBar: row.querySelector(".qty.bid .bar"),
      bidOrd: row.querySelector(".ord-bid"),
      askQv: row.querySelector(".qty.ask .qv"),
      askBar: row.querySelector(".qty.ask .bar"),
      askOrd: row.querySelector(".ord-ask"),
      last: { price: null, size: null },
    };
  }

  function buildLadder(n) {
    depth = n;
    el.asks.innerHTML = "";
    el.bids.innerHTML = "";
    askRows = [];
    bidRows = [];
    for (let i = 0; i < n; i++) {
      const a = makeRow("ask");
      askRows.push(a);
      el.asks.appendChild(a.el);
    }
    for (let i = 0; i < n; i++) {
      const b = makeRow("bid");
      bidRows.push(b);
      el.bids.appendChild(b.el);
    }
  }

  // Restart a CSS flash animation reliably.
  function flash(node, cls) {
    node.classList.remove(cls);
    void node.offsetWidth; // force reflow so the animation retriggers
    node.classList.add(cls);
  }

  // ── Render one snapshot (called from the rAF loop only) ─────
  function render(snap) {
    if (snap.depth && snap.depth !== depth) buildLadder(snap.depth);
    if (askRows.length !== snap.asks.length) buildLadder(snap.asks.length);

    // Instrument + stats
    el.instName.textContent = snap.name || snap.symbol;
    el.instSector.textContent = snap.sector || "—";

    const up = snap.change > 0, down = snap.change < 0;
    const dirCls = up ? "up" : down ? "down" : "";
    el.ltp.textContent = fmtPrice(snap.ltp);
    el.ltp.className = "stat-val " + dirCls;
    const sign = up ? "+" : "";
    el.change.textContent = `${sign}${fmtPrice(snap.change)} (${sign}${snap.change_pct}%)`;
    el.change.className = "stat-val " + dirCls;
    el.open.textContent = fmtPrice(snap.open);
    el.high.textContent = fmtPrice(snap.high);
    el.low.textContent = fmtPrice(snap.low);
    el.volume.textContent = fmtInt(snap.volume);
    el.value.textContent = fmtValue(snap.value);
    el.trades.textContent = fmtInt(snap.trades);
    el.spread.textContent = fmtPrice(snap.spread);

    // Scale bars to the deepest visible level on either side.
    let maxSize = 1;
    for (const lvl of snap.bids) if (lvl.size > maxSize) maxSize = lvl.size;
    for (const lvl of snap.asks) if (lvl.size > maxSize) maxSize = lvl.size;

    // Asks: render top → bottom as highest price → best (nearest mid).
    for (let k = 0; k < askRows.length; k++) {
      const lvl = snap.asks[askRows.length - 1 - k];
      paintRow(askRows[k], lvl, "ask", maxSize);
    }
    // Bids: best bid at the top (nearest mid).
    for (let k = 0; k < bidRows.length; k++) {
      paintRow(bidRows[k], snap.bids[k], "bid", maxSize);
    }

    // Center LTP + spread
    el.midLtp.textContent = fmtPrice(snap.ltp);
    el.midLtp.style.color = up ? "var(--up)" : down ? "var(--down)" : "var(--text)";
    el.midChange.textContent = `${sign}${fmtPrice(snap.change)} (${sign}${snap.change_pct}%)`;
    el.midChange.className = dirCls;
    el.midSpread.textContent = `spread ${fmtPrice(snap.spread)}`;
    if (prevLtp !== null && snap.ltp !== prevLtp) {
      flash(el.mid, snap.ltp > prevLtp ? "flash-up" : "flash-down");
    }
    prevLtp = snap.ltp;

    // Depth imbalance
    const total = (snap.bid_total || 0) + (snap.ask_total || 0) || 1;
    const bidPct = (snap.bid_total / total) * 100;
    el.imbBid.style.width = bidPct.toFixed(1) + "%";
    el.imbAsk.style.width = (100 - bidPct).toFixed(1) + "%";
    el.imbBidLbl.textContent = `Bids ${fmtInt(snap.bid_total)} (${bidPct.toFixed(0)}%)`;
    el.imbAskLbl.textContent = `${(100 - bidPct).toFixed(0)}% Asks ${fmtInt(snap.ask_total)}`;

    el.lastUpdate.textContent = new Date(snap.ts).toLocaleTimeString("en-GB");
  }

  function paintRow(row, lvl, side, maxSize) {
    const changed = row.last.price !== lvl.price || row.last.size !== lvl.size;
    row.price.textContent = fmtPrice(lvl.price);
    const widthPct = Math.max(2, Math.min(100, (lvl.size / maxSize) * 100));
    if (side === "ask") {
      row.askQv.textContent = fmtInt(lvl.size);
      row.askOrd.textContent = lvl.orders;
      row.askBar.style.width = widthPct + "%";
      row.bidQv.textContent = "";
      row.bidOrd.textContent = "";
      row.bidBar.style.width = "0";
    } else {
      row.bidQv.textContent = fmtInt(lvl.size);
      row.bidOrd.textContent = lvl.orders;
      row.bidBar.style.width = widthPct + "%";
      row.askQv.textContent = "";
      row.askOrd.textContent = "";
      row.askBar.style.width = "0";
    }
    if (changed) flash(row.el, side === "ask" ? "flash-ask" : "flash-bid");
    row.last.price = lvl.price;
    row.last.size = lvl.size;
  }

  // ── rAF render loop — decouples paint rate from message rate ─
  function frame() {
    if (dirty && latest) {
      render(latest);
      dirty = false;
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // ── Symbol picker ───────────────────────────────────────────
  function populateSymbols(list, def) {
    el.select.innerHTML = "";
    for (const s of list) {
      const opt = document.createElement("option");
      opt.value = s.symbol;
      opt.textContent = s.symbol;
      opt.title = s.name;
      el.select.appendChild(opt);
    }
    if (!currentSymbol) currentSymbol = def || (list[0] && list[0].symbol);
    if (currentSymbol) el.select.value = currentSymbol;
  }

  el.select.addEventListener("change", () => {
    currentSymbol = el.select.value;
    latest = null;
    prevLtp = null;
    for (const r of askRows.concat(bidRows)) r.last = { price: null, size: null };
    subscribe(currentSymbol);
  });

  function subscribe(symbol) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "subscribe", symbol }));
    }
  }

  // ── Connection lifecycle ────────────────────────────────────
  function setStatus(state, text) {
    el.dot.className = "dot " + (state === "live" ? "live" : state === "dead" ? "dead" : "");
    el.statusText.textContent = text;
  }

  let pingTimer = null;

  function connect() {
    setStatus("", "connecting…");
    el.feedInfo.textContent = "feed " + WS_URL;
    try {
      ws = new WebSocket(WS_URL);
    } catch (e) {
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      reconnectDelay = 500;
      setStatus("live", "live");
      if (currentSymbol) subscribe(currentSymbol);
      clearInterval(pingTimer);
      pingTimer = setInterval(() => subscribePing(), 25000);
    };

    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "meta") {
        populateSymbols(msg.symbols, msg.default);
        if (msg.depth) buildLadder(msg.depth);
        el.feedInfo.textContent = `feed ${WS_URL} · ${msg.tick_ms}ms · depth ${msg.depth}`;
        // If we defaulted the symbol only now, make sure we're subscribed to it.
        if (currentSymbol) subscribe(currentSymbol);
      } else if (msg.type === "snapshot") {
        if (!currentSymbol) { currentSymbol = msg.symbol; el.select.value = msg.symbol; }
        if (msg.symbol !== currentSymbol) return; // stale (mid symbol-switch)
        latest = msg;
        dirty = true;
      }
    };

    ws.onclose = () => {
      clearInterval(pingTimer);
      setStatus("dead", "disconnected — retrying");
      scheduleReconnect();
    };

    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  function subscribePing() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping", ts: Date.now() }));
    }
  }

  function scheduleReconnect() {
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 5000);
  }

  buildLadder(depth);
  connect();
})();
