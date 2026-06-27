"""Simulation router — mounts game and season sub-routers under /simulations."""
from fastapi import APIRouter

from app.api.routes.game import game_router
from app.api.routes.season import season_router

router = APIRouter(prefix="/simulations", tags=["simulations"])
router.include_router(game_router)
router.include_router(season_router)
