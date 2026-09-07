# DSEX DOM — Real-Time Depth of Market Tracker

A fully wired, zero-latency **Level-2 order book** for DSEX (Dhaka Stock
Exchange). One Python simulator feeds a FastAPI WebSocket broker, which streams
a normalized book to two interchangeable UIs: a **browser DOM ladder** and an
**ultra-low-latency terminal (curses) ladder**. Bids are green, asks are red,
and changed rows flash on every tick — without freezing.

```
 feeder (source of truth)  ──►  FastAPI broker  ──►  WebSocket /dom  ──►  UI
   feeder/feeder.py             backend/server.py     ws://…:8000/dom     frontend/  (browser)
                                                                          tui/       (terminal)
```

Because DSEX exposes no public real-time WebSocket, `feeder/feeder.py`
synthesises a realistic, **stateful** Level-2 book (persistent resting size per
level, mean-reverting price walk, OHLC/volume, a simplified ±10% circuit
breaker). Swap that one module for a real source later — the broker and both
UIs stay untouched.

---

## File structure

```
Dsex-DOM/
├── feeder/                     # ── Data layer (the source of truth)
│   ├── __init__.py
│   ├── instruments.py          # DSEX instrument universe (tickers, ticks, lots)
│   └── feeder.py               # MarketSimulator — Level-2 generator + CLI
│
├── backend/                    # ── Broker (FastAPI + WebSocket)
│   ├── __init__.py
│   ├── config.py               # env/.env config (host, port, cadence, CORS)
│   └── server.py               # imports feeder, broadcasts on ws://…/dom, serves frontend
│
├── frontend/                   # ── Browser UI (static — Hostinger-ready)
│   ├── index.html
│   ├── styles.css
│   ├── config.js               # WebSocket URL resolution (dev auto / prod override)
│   └── app.js                  # WS client + center price-ladder renderer
│
├── tui/                        # ── Terminal UI (curses)
│   └── dom_tui.py              # same WS feed, rendered in the terminal
│
├── requirements.txt
├── Procfile                    # Render start command
├── .env.example
├── run_backend.bat             # Windows one-shot: venv + deps + start broker
├── run_tui.bat                 # Windows one-shot: start terminal UI
├── serve_frontend.bat          # Windows: serve frontend standalone (Hostinger sim)
└── README.md
```

---

## One-shot execution (Windows 10)

### The fastest path — browser DOM (single command)

The backend **serves the frontend itself**, so you start one process and open
one URL.

```bat
run_backend.bat
```

Then open **http://127.0.0.1:8000/** in your browser. Live DOM. Done.
(First run creates a virtualenv and installs dependencies automatically.)

### The terminal DOM (second window)

With the backend already running:

```bat
run_tui.bat
:: or pick a symbol:  run_tui.bat --symbol BEXIMCO
```

Keys: `n`/`→` next symbol · `p`/`←` previous · `q` quit.

### Manual commands (no batch files)

```bat
py -3 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

:: Terminal 1 — backend broker (also serves the browser UI at http://127.0.0.1:8000/)
python -m backend.server

:: Terminal 2 — terminal UI  (optional; browser needs nothing extra)
python tui\dom_tui.py
```

### Inspect the raw feed (no server needed)

```bat
python -m feeder.feeder --symbol GP --interval 0.3
```

> **macOS / Linux:** identical, but use `python3 -m venv venv` and
> `source venv/bin/activate`. `windows-curses` installs only on Windows; the
> stdlib `curses` is already present elsewhere.

---

## How the wiring works

1. **Feeder** (`feeder/feeder.py`) — `MarketSimulator` holds one evolving
   `BookState` per instrument. `step()` advances every book one tick;
   `snapshot(symbol)` returns a normalized JSON-ready dict (`ltp`, `bids`,
   `asks`, `volume`, …).
2. **Broker** (`backend/server.py`) — `import`s the feeder directly (no network
   hop), runs a single background task that calls `sim.step()` every
   `DOM_TICK_MS`, then fan-out broadcasts each client the snapshot for the
   symbol it subscribed to. Clients switch symbols with
   `{"type":"subscribe","symbol":"GP"}`.
3. **Browser UI** (`frontend/`) — connects to `/dom`, keeps the latest snapshot,
   and paints on `requestAnimationFrame` (render rate is decoupled from message
   rate, so it never freezes). DOM rows are created once and mutated in place;
   only changed rows flash.
4. **Terminal UI** (`tui/dom_tui.py`) — same protocol, `websockets` async client
   + curses, ~30 fps, auto-reconnecting.

### Message protocol (`/dom`)

| Direction | Message |
|-----------|---------|
| server → client (on connect) | `{"type":"meta","symbols":[…],"default":"GP","tick_ms":300,"depth":10}` |
| server → client (per tick)   | `{"type":"snapshot","symbol":"GP","ltp":312.4,"bids":[…],"asks":[…],…}` |
| client → server              | `{"type":"subscribe","symbol":"BEXIMCO"}` · `{"type":"ping"}` |

---

## Configuration

Copy `.env.example` → `.env` (auto-loaded by `backend/config.py`), or set env
vars directly. On Windows: `set DOM_TICK_MS=200` before launching.

| Variable | Default | Meaning |
|----------|---------|---------|
| `DOM_HOST` | `127.0.0.1` | Bind address |
| `DOM_PORT` / `PORT` | `8000` | Port (`PORT` wins — Render sets it) |
| `DOM_TICK_MS` | `300` | Broadcast cadence (ms) |
| `DOM_DEPTH` | `10` | Price levels per side |
| `DOM_CORS_ORIGINS` | `*` | Allowed origins (comma-separated) |

---

## Deployment (no core rewrite required)

### Backend → Render

The architecture already binds to `0.0.0.0:$PORT`. Create a **Web Service**:

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn backend.server:app --host 0.0.0.0 --port $PORT`
  (a `Procfile` with the same command is included)
- Set `DOM_CORS_ORIGINS` to your frontend origin, e.g.
  `https://your-site.hostingersite.com`

Your feed is then `wss://<your-app>.onrender.com/dom`.

### Frontend → Hostinger

`frontend/` is pure static files — upload them to `public_html/`. Point the UI
at your backend by editing **`frontend/config.js`**:

```js
window.DOM_CONFIG = { WS_URL: "wss://your-app.onrender.com/dom" };
```

(Or append `?ws=wss://your-app.onrender.com/dom` to the URL for a quick test.)
Use `wss://` from an `https://` page — browsers block insecure `ws://` there.

---

## Notes

- The data is **simulated**. Prices/quantities are illustrative and are not real
  DSEX quotes. Replace `feeder/feeder.py` with a real ingestion source (broker
  API, scraper, vendor feed) that yields the same `snapshot()` shape and nothing
  downstream changes.
- The ±10% circuit band is a simplification; DSE applies tiered bands by price.
