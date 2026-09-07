"""
DSEX DOM — ultra-low-latency terminal UI (curses).

Connects to the same backend WebSocket as the web frontend and renders a
center-focused price ladder right in your terminal. Asks (red) stack above the
LTP, bids (green) below; changed rows flash briefly.

Windows note: the stdlib `curses` module is not bundled with Python on Windows.
`requirements.txt` installs `windows-curses` to provide it — no code change needed.

Run:
    python tui/dom_tui.py
    python tui/dom_tui.py --symbol BEXIMCO
    python tui/dom_tui.py --url ws://127.0.0.1:8000/dom

Keys:  n / →  next symbol      p / ←  previous symbol      q / Esc  quit
"""

from __future__ import annotations

import argparse
import asyncio
import curses
import json
import time

try:
    import websockets  # type: ignore
except ImportError:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'websockets'. Install it with:\n"
        "    pip install -r requirements.txt"
    )

DEFAULT_URL = "ws://127.0.0.1:8000/dom"

# curses color pair ids
C_RED = 1
C_GREEN = 2
C_DIM = 3
C_WHITE = 4
C_HEADER = 5
C_RED_HL = 6
C_GREEN_HL = 7


class Shared:
    """State shared between the receiver task and the render loop."""

    def __init__(self) -> None:
        self.snapshot: dict | None = None
        self.symbols: list[str] = []
        self.symbol_index: int = 0
        self.status: str = "connecting…"
        self.connected: bool = False
        self.pending_symbol: str | None = None   # requested via keyboard
        self.tick_ms: int = 0
        # per-row flash bookkeeping: key -> (value, expires_at)
        self.flash: dict[str, float] = {}
        self.prev_ltp: float | None = None
        self.ltp_flash_until: float = 0.0
        self.ltp_flash_dir: int = 0

    @property
    def current_symbol(self) -> str | None:
        if not self.symbols:
            return None
        return self.symbols[self.symbol_index % len(self.symbols)]


def _fmt_int(n) -> str:
    try:
        return f"{int(round(n)):,}"
    except (TypeError, ValueError):
        return "—"


def _fmt_value(v) -> str:
    if v is None:
        return "—"
    if v >= 1e7:
        return f"{v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"{v / 1e5:.2f} L"
    return f"{v:,.0f}"


async def receiver(ws, shared: Shared) -> None:
    """Consume server messages into shared state until the socket closes."""
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            continue
        kind = msg.get("type")
        if kind == "meta":
            shared.symbols = [s["symbol"] for s in msg.get("symbols", [])]
            shared.tick_ms = msg.get("tick_ms", 0)
            if shared.current_symbol is None and shared.symbols:
                shared.symbol_index = 0
        elif kind == "snapshot":
            if shared.current_symbol and msg.get("symbol") != shared.current_symbol:
                continue  # stale during a symbol switch
            _mark_flashes(shared, msg)
            shared.snapshot = msg


def _mark_flashes(shared: Shared, snap: dict) -> None:
    """Compare with the previous book and arm flashes for changed rows."""
    now = time.time()
    ttl = 0.45
    for side in ("bids", "asks"):
        for i, lvl in enumerate(snap.get(side, [])):
            key = f"{side}:{i}"
            token = f"{lvl['price']}:{lvl['size']}"
            if shared.flash.get(key) != token:
                shared.flash[key] = token
                shared.flash[key + ":until"] = now + ttl
    ltp = snap.get("ltp")
    if shared.prev_ltp is not None and ltp != shared.prev_ltp:
        shared.ltp_flash_dir = 1 if ltp > shared.prev_ltp else -1
        shared.ltp_flash_until = now + ttl
    shared.prev_ltp = ltp


def _flashing(shared: Shared, side: str, i: int) -> bool:
    return time.time() < shared.flash.get(f"{side}:{i}:until", 0.0)


def _safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
    """Write text without ever throwing on the bottom-right cell / overflow."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    try:
        win.addstr(y, x, text[: max(0, w - x - 1)], attr)
    except curses.error:
        pass


def draw(win, shared: Shared) -> None:
    win.erase()
    h, w = win.getmaxyx()
    snap = shared.snapshot

    dot = "●" if shared.connected else "○"
    status_attr = curses.color_pair(C_GREEN) if shared.connected else curses.color_pair(C_RED)
    _safe_addstr(win, 0, 1, "DSEX  Depth of Market", curses.color_pair(C_HEADER) | curses.A_BOLD)
    _safe_addstr(win, 0, w - 22, f"{dot} {shared.status}", status_attr)

    if not snap:
        _safe_addstr(win, 2, 1, "waiting for feed…", curses.color_pair(C_DIM))
        win.noutrefresh()
        return

    up = snap["change"] > 0
    down = snap["change"] < 0
    chg_attr = curses.color_pair(C_GREEN if up else C_RED if down else C_WHITE)
    sign = "+" if up else ""

    # ── Instrument + headline stats ─────────────────────────────
    _safe_addstr(win, 1, 1, f"{snap['symbol']:<11}", curses.color_pair(C_WHITE) | curses.A_BOLD)
    _safe_addstr(win, 1, 13, f"{snap.get('name', '')}", curses.color_pair(C_DIM))
    _safe_addstr(win, 2, 1, f"LTP {snap['ltp']:>10.2f}", curses.color_pair(C_WHITE) | curses.A_BOLD)
    _safe_addstr(win, 2, 24, f"{sign}{snap['change']:.2f} ({sign}{snap['change_pct']}%)", chg_attr | curses.A_BOLD)
    _safe_addstr(
        win, 3, 1,
        f"O {snap['open']:.2f}  H {snap['high']:.2f}  L {snap['low']:.2f}  "
        f"Vol {_fmt_int(snap['volume'])}  Val {_fmt_value(snap['value'])}  "
        f"Trd {_fmt_int(snap['trades'])}  Spr {snap['spread']:.2f}",
        curses.color_pair(C_DIM),
    )

    asks = snap["asks"]
    bids = snap["bids"]
    n = len(asks)

    # Column layout centered on the terminal.
    price_col = max(20, w // 2 - 4)
    qty_w = min(18, price_col - 10)
    max_size = max([1] + [l["size"] for l in asks] + [l["size"] for l in bids])

    header_y = 5
    _safe_addstr(win, header_y, price_col - qty_w - 8, "Bid Qty", curses.color_pair(C_DIM))
    _safe_addstr(win, header_y, price_col, "Price", curses.color_pair(C_DIM))
    _safe_addstr(win, header_y, price_col + 9, "Ask Qty", curses.color_pair(C_DIM))

    # ── Ask block: highest price at top, best ask just above mid ─
    top = header_y + 1
    for k in range(n):
        lvl = asks[n - 1 - k]
        idx = n - 1 - k
        y = top + k
        flash = _flashing(shared, "asks", idx)
        attr = curses.color_pair(C_RED_HL if flash else C_RED)
        bar = _bar(lvl["size"], max_size, qty_w)
        _safe_addstr(win, y, price_col - qty_w - 8, f"{_fmt_int(lvl['size']):>10}", attr)
        _safe_addstr(win, y, price_col + 9, bar, curses.color_pair(C_RED))
        _safe_addstr(win, y, price_col, f"{lvl['price']:>7.2f}", attr | curses.A_BOLD)

    # ── Mid / LTP ───────────────────────────────────────────────
    mid_y = top + n
    ltp_flash = time.time() < shared.ltp_flash_until
    if ltp_flash:
        mid_attr = curses.color_pair(C_GREEN_HL if shared.ltp_flash_dir > 0 else C_RED_HL)
    else:
        mid_attr = chg_attr
    _safe_addstr(win, mid_y, price_col - qty_w - 8, "─" * (qty_w + 8), curses.color_pair(C_DIM))
    _safe_addstr(win, mid_y, price_col, f"{snap['ltp']:>7.2f}", mid_attr | curses.A_BOLD)
    _safe_addstr(win, mid_y, price_col + 9, f"◄ LTP  spr {snap['spread']:.2f}", curses.color_pair(C_DIM))

    # ── Bid block: best bid just below mid ──────────────────────
    for k in range(n):
        lvl = bids[k]
        y = mid_y + 1 + k
        flash = _flashing(shared, "bids", k)
        attr = curses.color_pair(C_GREEN_HL if flash else C_GREEN)
        bar = _bar(lvl["size"], max_size, qty_w)
        _safe_addstr(win, y, price_col - qty_w - 8, f"{_fmt_int(lvl['size']):>10}", attr)
        _safe_addstr(win, y, price_col - qty_w + 1, "", 0)
        _safe_addstr(win, y, price_col + 9, bar, curses.color_pair(C_GREEN))
        _safe_addstr(win, y, price_col, f"{lvl['price']:>7.2f}", attr | curses.A_BOLD)

    # ── Imbalance + footer ──────────────────────────────────────
    total = (snap.get("bid_total", 0) + snap.get("ask_total", 0)) or 1
    bid_pct = snap.get("bid_total", 0) / total * 100
    foot_y = mid_y + n + 2
    _safe_addstr(
        win, foot_y, 1,
        f"Imbalance  Bids {bid_pct:5.1f}%  |  Asks {100 - bid_pct:5.1f}%",
        curses.color_pair(C_WHITE),
    )
    _safe_addstr(
        win, min(h - 1, foot_y + 1), 1,
        "n/→ next   p/← prev   q quit"
        + (f"   ·   {shared.tick_ms}ms feed" if shared.tick_ms else ""),
        curses.color_pair(C_DIM),
    )
    win.noutrefresh()


def _bar(size: int, max_size: int, width: int) -> str:
    filled = int(round((size / max_size) * width)) if max_size else 0
    return "█" * max(0, min(width, filled))


def handle_input(win, shared: Shared) -> bool:
    """Poll keyboard (non-blocking). Returns False to quit."""
    try:
        ch = win.getch()
    except curses.error:
        ch = -1
    if ch == -1:
        return True
    if ch in (ord("q"), ord("Q"), 27):  # q or Esc
        return False
    if not shared.symbols:
        return True
    if ch in (ord("n"), ord("N"), curses.KEY_RIGHT, curses.KEY_DOWN):
        shared.symbol_index = (shared.symbol_index + 1) % len(shared.symbols)
        shared.pending_symbol = shared.current_symbol
    elif ch in (ord("p"), ord("P"), curses.KEY_LEFT, curses.KEY_UP):
        shared.symbol_index = (shared.symbol_index - 1) % len(shared.symbols)
        shared.pending_symbol = shared.current_symbol
    return True


def _init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_RED, curses.COLOR_RED, -1)
    curses.init_pair(C_GREEN, curses.COLOR_GREEN, -1)
    curses.init_pair(C_DIM, curses.COLOR_CYAN, -1)
    curses.init_pair(C_WHITE, curses.COLOR_WHITE, -1)
    curses.init_pair(C_HEADER, curses.COLOR_YELLOW, -1)
    curses.init_pair(C_RED_HL, curses.COLOR_WHITE, curses.COLOR_RED)
    curses.init_pair(C_GREEN_HL, curses.COLOR_BLACK, curses.COLOR_GREEN)


async def session(win, shared: Shared, url: str) -> None:
    """One connection: subscribe, then render at ~30fps while receiving."""
    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
        shared.connected = True
        shared.status = "live"
        recv = asyncio.create_task(receiver(ws, shared))
        try:
            # Wait briefly for the meta handshake, then subscribe explicitly.
            for _ in range(50):
                if shared.symbols:
                    break
                await asyncio.sleep(0.02)
            if shared.current_symbol:
                await ws.send(json.dumps({"type": "subscribe", "symbol": shared.current_symbol}))

            while True:
                if not handle_input(win, shared):
                    recv.cancel()
                    raise KeyboardInterrupt
                if shared.pending_symbol:
                    await ws.send(json.dumps({"type": "subscribe", "symbol": shared.pending_symbol}))
                    shared.pending_symbol = None
                    shared.snapshot = None
                    shared.prev_ltp = None
                    shared.flash.clear()
                draw(win, shared)
                curses.doupdate()
                if recv.done():
                    break
                await asyncio.sleep(0.033)
        finally:
            recv.cancel()


async def run(win, url: str, start_symbol: str | None) -> None:
    curses.curs_set(0)
    win.nodelay(True)
    win.keypad(True)
    _init_colors()

    shared = Shared()
    if start_symbol:
        shared.symbols = [start_symbol]  # provisional until meta arrives

    while True:
        try:
            await session(win, shared, url)
        except KeyboardInterrupt:
            return
        except Exception as exc:  # connection dropped / refused
            shared.connected = False
            shared.status = f"reconnecting… ({type(exc).__name__})"
            for _ in range(30):  # ~1s, staying responsive to 'q'
                if not handle_input(win, shared):
                    return
                draw(win, shared)
                curses.doupdate()
                await asyncio.sleep(0.033)


def main() -> None:
    parser = argparse.ArgumentParser(description="DSEX DOM terminal UI")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"WebSocket URL (default: {DEFAULT_URL})")
    parser.add_argument("--symbol", default=None, help="Initial symbol (e.g. GP)")
    args = parser.parse_args()

    url = args.url
    if args.symbol:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}symbol={args.symbol}"

    def _entry(stdscr):
        asyncio.run(run(stdscr, url, args.symbol))

    try:
        curses.wrapper(_entry)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
