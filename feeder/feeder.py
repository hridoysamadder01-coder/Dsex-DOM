"""
DSEX Level-2 market data simulator -- the absolute source of truth.

This is a standalone, dependency-free data ingestion module. Because DSEX does
not expose a public real-time WebSocket, `MarketSimulator` synthesises a live,
self-consistent Level-2 order book (bids, asks, LTP, OHLC, volume) for every
instrument in the universe.

The book is *stateful*: each `step()` mutates only a few price levels, so most
of the ladder persists frame-to-frame and only the changed rows "flash" in the
UI -- exactly like a real DOM.

Run it standalone to eyeball the raw feed:

    python -m feeder.feeder            # stream default symbol
    python -m feeder.feeder --symbol BEXIMCO --interval 0.3
"""

from __future__ import annotations

import argparse
import json
import random
import time
from typing import Optional

from feeder.instruments import DSEX_UNIVERSE, Instrument, UNIVERSE_BY_SYMBOL

DEFAULT_DEPTH = 10          # price levels per side
CIRCUIT_PCT = 0.10         # simplified +/-10% daily circuit breaker (DSE uses tiered bands)
MEAN_REVERSION = 0.04      # how strongly LTP is pulled back toward fair value


def _round_tick(price: float, tick: float) -> float:
    """Snap a price onto the instrument tick grid."""
    return round(round(price / tick) * tick, 2)


class BookState:
    """Mutable, evolving state for one instrument's order book."""

    def __init__(self, inst: Instrument, depth: int, rng: random.Random) -> None:
        self.inst = inst
        self.depth = depth
        self.rng = rng

        self.prev_close = inst.base_price
        self.fair = inst.base_price
        self.ltp = inst.base_price
        self.open = inst.base_price
        self.high = inst.base_price
        self.low = inst.base_price
        self.volume = 0
        self.value = 0.0
        self.trades = 0
        self.direction = 0  # +1 uptick, -1 downtick, 0 unchanged

        self.circuit_up = _round_tick(self.prev_close * (1 + CIRCUIT_PCT), inst.tick)
        self.circuit_down = _round_tick(self.prev_close * (1 - CIRCUIT_PCT), inst.tick)

        # Persistent resting sizes / order counts per level (index 0 == best).
        self.bid_sizes = [self._fresh_size(i) for i in range(depth)]
        self.ask_sizes = [self._fresh_size(i) for i in range(depth)]
        self.bid_orders = [self._fresh_orders() for _ in range(depth)]
        self.ask_orders = [self._fresh_orders() for _ in range(depth)]

        # Warm up so the very first snapshot already looks "mid-session".
        for _ in range(30):
            self.step()
        self.open = self.ltp
        self.high = max(self.high, self.ltp)
        self.low = min(self.low, self.ltp)

    # -- size / order helpers -------------------------------------------------
    def _fresh_size(self, level: int) -> int:
        """Resting size for a level; deeper levels tend to be larger."""
        depth_factor = 1.0 + 0.28 * level
        raw = self.inst.liquidity * depth_factor * self.rng.uniform(0.35, 1.65)
        lots = max(1, int(raw / self.inst.lot))
        return lots * self.inst.lot

    def _fresh_orders(self) -> int:
        return self.rng.randint(1, 18)

    # -- evolution ------------------------------------------------------------
    def step(self) -> None:
        """Advance this book by one tick of simulated time."""
        inst = self.inst

        # 1) Drift fair value on a slow random walk, then pull LTP toward it.
        self.fair += self.rng.gauss(0.0, self.fair * inst.volatility * 0.30)
        self.fair = min(max(self.fair, self.circuit_down), self.circuit_up)

        drift = (self.fair - self.ltp) * MEAN_REVERSION
        shock = self.rng.gauss(0.0, self.ltp * inst.volatility)
        # Occasional larger prints to keep it lively.
        if self.rng.random() < 0.04:
            shock *= self.rng.uniform(2.5, 4.5)

        new_ltp = self.ltp + drift + shock
        new_ltp = min(max(new_ltp, self.circuit_down), self.circuit_up)
        new_ltp = _round_tick(new_ltp, inst.tick)
        if new_ltp <= 0:
            new_ltp = inst.tick

        self.direction = (new_ltp > self.ltp) - (new_ltp < self.ltp)
        self.ltp = new_ltp
        self.high = max(self.high, self.ltp)
        self.low = min(self.low, self.ltp)

        # 2) Book a trade: consume some size from the touch, accrue volume/value.
        traded_lots = self.rng.randint(1, 40)
        traded = traded_lots * inst.lot
        self.volume += traded
        self.value += traded * self.ltp
        self.trades += 1

        # 3) Mutate a handful of resting levels (order add / cancel) so only a
        #    few rows change each frame -> targeted flashing in the UI.
        for sizes, orders in ((self.bid_sizes, self.bid_orders),
                              (self.ask_sizes, self.ask_orders)):
            for _ in range(self.rng.randint(1, 3)):
                lvl = self.rng.randrange(self.depth)
                if self.rng.random() < 0.30:
                    sizes[lvl] = self._fresh_size(lvl)          # full refresh
                    orders[lvl] = self._fresh_orders()
                else:
                    delta_lots = self.rng.randint(-6, 8) * max(1, inst.liquidity // 20 // inst.lot)
                    new_size = sizes[lvl] + delta_lots * inst.lot
                    floor = inst.lot * max(1, inst.liquidity // inst.lot // 20)
                    sizes[lvl] = max(floor, new_size)
                    orders[lvl] = max(1, orders[lvl] + self.rng.randint(-2, 2))

    # -- projection -----------------------------------------------------------
    def _touch(self) -> tuple[float, float]:
        """Best bid / best ask straddling the last traded price."""
        inst = self.inst
        base = _round_tick(self.ltp, inst.tick)
        best_bid = base - inst.tick
        best_ask = base
        if self.rng.random() < 0.22:          # occasionally widen to a 2-tick spread
            best_ask = base + inst.tick
        best_bid = max(inst.tick, best_bid)
        return best_bid, best_ask

    def snapshot(self) -> dict:
        """Return a normalized Level-2 snapshot ready to broadcast."""
        inst = self.inst
        best_bid, best_ask = self._touch()

        bids = [
            {
                "price": _round_tick(best_bid - i * inst.tick, inst.tick),
                "size": self.bid_sizes[i],
                "orders": self.bid_orders[i],
            }
            for i in range(self.depth)
        ]
        asks = [
            {
                "price": _round_tick(best_ask + i * inst.tick, inst.tick),
                "size": self.ask_sizes[i],
                "orders": self.ask_orders[i],
            }
            for i in range(self.depth)
        ]

        change = round(self.ltp - self.prev_close, 2)
        change_pct = round((change / self.prev_close) * 100, 2) if self.prev_close else 0.0
        bid_total = sum(self.bid_sizes)
        ask_total = sum(self.ask_sizes)

        return {
            "symbol": inst.symbol,
            "name": inst.name,
            "sector": inst.sector,
            "ts": int(time.time() * 1000),
            "ltp": self.ltp,
            "prev_close": round(self.prev_close, 2),
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "change": change,
            "change_pct": change_pct,
            "volume": self.volume,
            "value": round(self.value, 2),
            "trades": self.trades,
            "spread": round(best_ask - best_bid, 2),
            "dir": self.direction,
            "circuit_up": self.circuit_up,
            "circuit_down": self.circuit_down,
            "tick": inst.tick,
            "lot": inst.lot,
            "bids": bids,
            "asks": asks,
            "bid_total": bid_total,
            "ask_total": ask_total,
        }


class MarketSimulator:
    """Owns every instrument's book and advances them in lockstep."""

    def __init__(self, universe: Optional[list[Instrument]] = None,
                 depth: int = DEFAULT_DEPTH, seed: Optional[int] = None) -> None:
        universe = universe or DSEX_UNIVERSE
        self.depth = depth
        self.rng = random.Random(seed)
        self.books: dict[str, BookState] = {
            inst.symbol: BookState(inst, depth, random.Random(self.rng.random()))
            for inst in universe
        }
        self.symbols: list[str] = [inst.symbol for inst in universe]
        self.default_symbol: str = self.symbols[0]

    def step(self) -> None:
        """Advance every book by one tick."""
        for book in self.books.values():
            book.step()

    def snapshot(self, symbol: str) -> dict:
        """Normalized Level-2 snapshot for one symbol (falls back to default)."""
        book = self.books.get(symbol) or self.books[self.default_symbol]
        return book.snapshot()

    def symbol_meta(self) -> list[dict]:
        """Lightweight directory of instruments for UI symbol pickers."""
        return [
            {"symbol": b.inst.symbol, "name": b.inst.name, "sector": b.inst.sector}
            for b in self.books.values()
        ]


def create_default_simulator(depth: int = DEFAULT_DEPTH,
                             seed: Optional[int] = None) -> MarketSimulator:
    """Factory used by the backend broker."""
    return MarketSimulator(DSEX_UNIVERSE, depth=depth, seed=seed)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Stream raw DSEX mock Level-2 data to stdout.")
    parser.add_argument("--symbol", default=None, help="Ticker to stream (default: first in universe)")
    parser.add_argument("--interval", type=float, default=0.3, help="Seconds between ticks")
    parser.add_argument("--depth", type=int, default=DEFAULT_DEPTH, help="Price levels per side")
    args = parser.parse_args()

    sim = create_default_simulator(depth=args.depth)
    symbol = args.symbol if args.symbol in UNIVERSE_BY_SYMBOL else sim.default_symbol
    print(f"# streaming {symbol}  (depth={args.depth}, interval={args.interval}s) -- Ctrl+C to stop")
    try:
        while True:
            sim.step()
            snap = sim.snapshot(symbol)
            best_bid = snap["bids"][0]
            best_ask = snap["asks"][0]
            print(json.dumps({
                "ts": snap["ts"], "symbol": snap["symbol"], "ltp": snap["ltp"],
                "chg%": snap["change_pct"], "bid": [best_bid["price"], best_bid["size"]],
                "ask": [best_ask["price"], best_ask["size"]], "vol": snap["volume"],
            }))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n# stopped")


if __name__ == "__main__":
    _cli()
