"""
DSEX instrument universe.

DSEX (Dhaka Stock Exchange Broad Index) has no public Level-2 WebSocket feed,
so this module defines the instrument metadata that the mock simulator uses to
generate a realistic, self-consistent Level-2 book for each ticker.

Prices are illustrative anchors in BDT (Bangladeshi Taka). Swap `base_price`
for a real snapshot when you wire in an actual data source -- nothing else in
the pipeline needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    """Static definition of a tradable DSEX security."""

    symbol: str          # DSE trading code
    name: str            # Full company name
    sector: str          # DSE sector bucket
    base_price: float    # Anchor / previous-close seed (BDT)
    tick: float          # Minimum price increment (BDT)
    lot: int             # Minimum quantity increment (shares)
    volatility: float    # Per-step relative volatility (std dev fraction)
    liquidity: int       # Typical resting size at a single price level


# Illustrative DSEX blue-chips + high-volume movers.
# base_price values are representative anchors, not live quotes.
DSEX_UNIVERSE: list[Instrument] = [
    Instrument("GP",         "Grameenphone Ltd.",              "Telecommunication", 312.40, 0.10, 10, 0.00120,  4_000),
    Instrument("SQURPHARMA", "Square Pharmaceuticals PLC",     "Pharmaceuticals",   214.80, 0.10, 10, 0.00150,  6_000),
    Instrument("BEXIMCO",    "Bangladesh Export Import Co.",   "Miscellaneous",     104.30, 0.10, 50, 0.00320, 20_000),
    Instrument("BATBC",      "British American Tobacco BD",    "Food & Allied",     512.60, 0.10,  1, 0.00110,    600),
    Instrument("ROBI",       "Robi Axiata Ltd.",               "Telecommunication",  28.70, 0.10, 50, 0.00380, 30_000),
    Instrument("WALTONHIL",  "Walton Hi-Tech Industries PLC",  "Engineering",       548.90, 0.10,  5, 0.00140,    900),
    Instrument("RENATA",     "Renata PLC",                     "Pharmaceuticals",   978.40, 0.10,  1, 0.00150,    350),
    Instrument("BRACBANK",   "BRAC Bank PLC",                  "Bank",               54.20, 0.10, 50, 0.00280, 18_000),
    Instrument("LHBL",       "LafargeHolcim Bangladesh PLC",   "Cement",             68.90, 0.10, 20, 0.00260, 12_000),
    Instrument("BEACONPHAR", "Beacon Pharmaceuticals PLC",     "Pharmaceuticals",   238.10, 0.10, 10, 0.00300,  9_000),
]

# Fast lookup by symbol.
UNIVERSE_BY_SYMBOL: dict[str, Instrument] = {i.symbol: i for i in DSEX_UNIVERSE}
