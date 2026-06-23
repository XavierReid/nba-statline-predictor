"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import games, players
from app.config import settings


logging.basicConfig(level=settings.log_level)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("NBA Franchise Simulator starting up — log level=%s", settings.log_level)
    yield
    log.info("NBA Franchise Simulator shutting down")


app = FastAPI(
    title="NBA Franchise Simulator",
    description="Backend simulation engine for NBA seasons, playoffs, and franchise management.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


app.include_router(players.router)
app.include_router(games.router)
