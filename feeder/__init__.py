"""
DSEX data ingestion package (the source of truth).

Import the concrete symbols from the submodules, e.g.:

    from feeder.feeder import MarketSimulator, create_default_simulator
    from feeder.instruments import DSEX_UNIVERSE

(Kept import-light on purpose so `python -m feeder.feeder` runs without a
runpy double-import warning.)
"""
