"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("abbreviation", sa.String(8), nullable=False, unique=True),
        sa.Column("city", sa.String(64), nullable=False),
        sa.Column("nickname", sa.String(64), nullable=False),
        sa.Column("conference", sa.String(16)),
        sa.Column("division", sa.String(32)),
    )

    op.create_table(
        "players",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("full_name", sa.String(128), nullable=False),
        sa.Column("team_id", sa.Integer, sa.ForeignKey("teams.id")),
        sa.Column("position", sa.String(8)),
    )
    op.create_index("ix_players_full_name", "players", ["full_name"])
    op.create_index("ix_players_team_id", "players", ["team_id"])

    op.create_table(
        "games",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("game_date", sa.Date, nullable=False),
        sa.Column("home_team_id", sa.Integer, sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("away_team_id", sa.Integer, sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("home_score", sa.Integer),
        sa.Column("away_score", sa.Integer),
        sa.Column("status", sa.String(16), nullable=False, server_default="scheduled"),
    )
    op.create_index("ix_games_game_date", "games", ["game_date"])
    op.create_index("ix_games_home_team_id", "games", ["home_team_id"])
    op.create_index("ix_games_away_team_id", "games", ["away_team_id"])


def downgrade() -> None:
    op.drop_table("games")
    op.execute("DROP TYPE IF EXISTS game_status")
    op.drop_table("players")
    op.drop_table("teams")
