from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import SessionLocal, init_db
from .security import ensure_admin_user, seed_if_empty
from . import services
from .ws import websocket_endpoint

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("carta")

_WEB_CANDIDATES = (
    Path(__file__).resolve().parent.parent.parent / "web",  # repo root /web (dev)
    Path(__file__).resolve().parent.parent / "web",  # /app/web (Docker)
    Path("/app/web"),
)
WEB_DIR = next((p for p in _WEB_CANDIDATES if p.is_dir()), _WEB_CANDIDATES[0])


async def shift_rollover_loop() -> None:
    while True:
        try:
            async with SessionLocal() as session:
                await services.ensure_current_shift(session)
        except Exception as exc:
            logger.warning("Shift rollover error: %s", exc)
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        await seed_if_empty(session)
        await ensure_admin_user(session)
        await services.ensure_current_shift(session)
    logger.info("Database ready (sqlite)")
    rollover_task = asyncio.create_task(shift_rollover_loop())
    try:
        yield
    finally:
        rollover_task.cancel()
        try:
            await rollover_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Автосервис CARTA", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "app": "CARTA"})


@app.websocket("/ws")
async def ws_route(websocket: WebSocket) -> None:
    await websocket_endpoint(websocket)


if WEB_DIR.is_dir():
    app.mount("/css", StaticFiles(directory=str(WEB_DIR / "css")), name="web-css")
    app.mount("/js", StaticFiles(directory=str(WEB_DIR / "js")), name="web-js")

    @app.get("/")
    async def web_index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
else:
    logger.warning("Web UI directory not found: %s", WEB_DIR)


def main() -> None:
    settings = get_settings()
    kwargs: dict = {
        "app": "app.main:app",
        "host": settings.carta_host,
        "port": settings.carta_port,
        "reload": False,
    }
    if settings.carta_use_tls:
        cert = Path(settings.tls_cert)
        key = Path(settings.tls_key)
        if cert.exists() and key.exists():
            kwargs["ssl_certfile"] = str(cert)
            kwargs["ssl_keyfile"] = str(key)
            logger.info("TLS enabled — clients should use wss://")
        else:
            logger.warning("TLS requested but cert/key missing; starting plain WS")
    uvicorn.run(**kwargs)


if __name__ == "__main__":
    main()
