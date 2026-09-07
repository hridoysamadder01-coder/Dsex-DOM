"""
DSEX DOM broker -- FastAPI + WebSocket.

Pipeline role: this process imports the `feeder` module directly (no network
hop -- the feeder is the source of truth), steps the simulator on a fixed
cadence, and fan-out broadcasts each connected client its subscribed symbol
over `ws://<host>:<port>/dom`.

It also serves the static `frontend/` at `/` so the whole system is a single
one-shot launch in development. In production you can host the frontend
separately (Hostinger) and point it at this backend (Render) -- the WebSocket
URL is the only thing that changes.

Run:
    python -m backend.server
    # or
    uvicorn backend.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from feeder.feeder import create_default_simulator

# --------------------------------------------------------------------------
# Source of truth: one simulator shared by every connection.
# --------------------------------------------------------------------------
sim = create_default_simulator(depth=config.DEPTH)


class ConnectionManager:
    """Tracks live WebSocket clients and the symbol each one is watching."""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, symbol: str) -> None:
        await ws.accept()
        async with self._lock:
            self._clients[ws] = symbol

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(ws, None)

    async def set_symbol(self, ws: WebSocket, symbol: str) -> None:
        async with self._lock:
            if ws in self._clients:
                self._clients[ws] = symbol

    async def targets(self) -> list[tuple[WebSocket, str]]:
        async with self._lock:
            return list(self._clients.items())

    @property
    def count(self) -> int:
        return len(self._clients)


manager = ConnectionManager()


async def _broadcaster() -> None:
    """Step the simulator and push each client its subscribed snapshot."""
    interval = max(0.02, config.TICK_MS / 1000.0)
    while True:
        sim.step()
        targets = await manager.targets()
        if targets:
            # Serialize each needed symbol once, then fan out.
            wanted = {symbol for _, symbol in targets}
            payloads = {
                symbol: json.dumps({"type": "snapshot", **sim.snapshot(symbol)})
                for symbol in wanted
            }

            async def _send(ws: WebSocket, symbol: str) -> None:
                try:
                    await ws.send_text(payloads[symbol])
                except Exception:
                    await manager.disconnect(ws)

            await asyncio.gather(
                *(_send(ws, symbol) for ws, symbol in targets),
                return_exceptions=True,
            )
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_broadcaster())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="DSEX DOM Broker", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "symbols": len(sim.symbols),
            "clients": manager.count,
            "tick_ms": config.TICK_MS,
            "depth": config.DEPTH,
        }
    )


@app.get("/api/symbols")
async def symbols() -> JSONResponse:
    return JSONResponse({"symbols": sim.symbol_meta(), "default": sim.default_symbol})


@app.websocket(config.WS_PATH)
async def dom_ws(ws: WebSocket) -> None:
    """Live DOM stream. Optional `?symbol=GP`; switch with {"type":"subscribe"}."""
    requested = ws.query_params.get("symbol", sim.default_symbol)
    symbol = requested if requested in sim.symbols else sim.default_symbol

    await manager.connect(ws, symbol)
    try:
        # Handshake: instrument directory + feed metadata, then an instant book.
        await ws.send_text(json.dumps({
            "type": "meta",
            "symbols": sim.symbol_meta(),
            "default": sim.default_symbol,
            "tick_ms": config.TICK_MS,
            "depth": config.DEPTH,
        }))
        await ws.send_text(json.dumps({"type": "snapshot", **sim.snapshot(symbol)}))

        while True:
            message = await ws.receive_text()
            try:
                data = json.loads(message)
            except (ValueError, TypeError):
                continue

            kind = data.get("type")
            if kind == "subscribe":
                new_symbol = data.get("symbol")
                if new_symbol in sim.symbols:
                    await manager.set_symbol(ws, new_symbol)
                    await ws.send_text(json.dumps({"type": "snapshot", **sim.snapshot(new_symbol)}))
            elif kind == "ping":
                await ws.send_text(json.dumps({"type": "pong", "ts": data.get("ts")}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await manager.disconnect(ws)


# --------------------------------------------------------------------------
# Serve the static frontend LAST so it never shadows the API/WebSocket routes.
# Skipped automatically if the folder is absent (e.g. backend-only on Render).
# --------------------------------------------------------------------------
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.server:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        log_level="info",
    )
