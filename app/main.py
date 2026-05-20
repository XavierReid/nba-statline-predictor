"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import backtest, games, players, predictions
from app.config import settings


logging.basicConfig(level=settings.log_level)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("NBA Statline Predictor starting up — log level=%s", settings.log_level)
    yield
    log.info("NBA Statline Predictor shutting down")


app = FastAPI(
    title="NBA Statline Predictor",
    description=(
        "Predicts player statlines for upcoming NBA games using rule-based heuristics "
        "over historical performance. Returns predictions with full factor breakdowns "
        "so every number is explainable."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


app.include_router(players.router)
app.include_router(games.router)
app.include_router(predictions.router)
app.include_router(backtest.router)
