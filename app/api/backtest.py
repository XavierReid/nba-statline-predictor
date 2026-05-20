"""Backtest endpoint — re-runs predictions for a past date and compares to actuals.

This is the killer demo feature: prove the predictor works (or honestly admit where it doesn't).
"""

from datetime import date as date_cls

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.get("")
def run_backtest(
    target_date: date_cls = Query(..., alias="date", description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """For every game on `date`, re-run predictions for all players and compare to actuals.

    Returns aggregated error metrics (MAE per stat) plus per-prediction rows for inspection.
    """
    # TODO: implement
    return {
        "date": target_date.isoformat(),
        "summary": {
            "games_evaluated": 0,
            "predictions": 0,
            "mae_points": None,
            "mae_rebounds": None,
            "mae_assists": None,
        },
        "rows": [],
    }
