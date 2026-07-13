"""Shared helper utilities for simulation API routes."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.game import Game
from app.models.simulation import SimulatedGame
from app.models.team import Team
from app.services.game_simulator import load_roster
from app.services.franchise import resolve_abbreviation
from app.api.schemas.simulations import PlayerLine, StepThroughResponse


def get_team(db: Session, abbr: str, season: Optional[str] = None) -> Team:
    # Season-aware: an era abbreviation (e.g. 'SEA' for 2005-06) resolves to the
    # franchise even though the teams table stores only today's identity ('OKC').
    if season:
        franchise_id = resolve_abbreviation(abbr, season)
        if franchise_id is not None:
            team = db.get(Team, franchise_id)
            if team:
                return team
    team = db.execute(select(Team).where(Team.abbreviation == abbr.upper())).scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{abbr}' not found")
    return team


def load_rosters(db: Session, home_team_abbr: str, away_team_abbr: str, season: str) -> tuple:
    if home_team_abbr.upper() == away_team_abbr.upper():
        raise HTTPException(status_code=422, detail="Home and away teams must be different.")
    home_team = get_team(db, home_team_abbr, season)
    away_team = get_team(db, away_team_abbr, season)
    home_players = load_roster(db, home_team.id, season)
    away_players = load_roster(db, away_team.id, season)
    if not home_players:
        raise HTTPException(422, detail=f"No roster data for {home_team_abbr} in season {season}. Run ingestion first.")
    if not away_players:
        raise HTTPException(422, detail=f"No roster data for {away_team_abbr} in season {season}. Run ingestion first.")
    return home_players, away_players


def build_box(players: list, box: dict) -> list[PlayerLine]:
    lines = []
    for p in players:
        s = box.get(p["id"], {})
        if s.get("min", 0) < 0.5:
            continue
        lines.append(PlayerLine(
            player_id=p["id"],
            name=p["name"],
            minutes=round(s.get("min", 0), 1),
            points=s.get("pts", 0),
            rebounds=s.get("reb", 0),
            assists=s.get("ast", 0),
            steals=s.get("stl", 0),
            blocks=s.get("blk", 0),
            turnovers=s.get("tov", 0),
            personal_fouls=s.get("pf", 0),
            plus_minus=s.get("plus_minus", 0),
            fgm=s.get("fgm", 0),
            fga=s.get("fga", 0),
            fg3m=s.get("fg3m", 0),
            fg3a=s.get("fg3a", 0),
            ftm=s.get("ftm", 0),
            fta=s.get("fta", 0),
            fouled_out=s.get("fouled_out", False),
        ))
    return sorted(lines, key=lambda l: l.points, reverse=True)


def build_stepthrough_response(token: str, data: dict) -> StepThroughResponse:
    chunk = data["chunk"]
    return StepThroughResponse(
        token=token,
        step=data["step"],
        total_steps=data["total_steps"],
        complete=data["complete"],
        elapsed_minutes=chunk["elapsed_minutes"],
        quarter=chunk["quarter"],
        season=data["season"],
        seed=data["seed"],
        home_team=data["home_team"],
        away_team=data["away_team"],
        home_score=chunk["home_score"],
        away_score=chunk["away_score"],
        home_box=build_box(data["home_players"], chunk["box"]),
        away_box=build_box(data["away_players"], chunk["box"]),
    )


def sim_game_is_win(db: Session, sg: SimulatedGame, team_id: int) -> bool:
    real_game = db.get(Game, sg.game_id)
    is_home = real_game.home_team_id == team_id
    return (sg.home_score > sg.away_score) if is_home else (sg.away_score > sg.home_score)
