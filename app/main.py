"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import games, ingestion, players, simulations, teams
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


# CORS for the local Vite dev server (frontend/). Tighten allow_origins for any deploy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(players.router)
app.include_router(games.router)
app.include_router(simulations.router)
app.include_router(ingestion.router)
app.include_router(teams.router)
