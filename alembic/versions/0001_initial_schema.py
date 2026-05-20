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
        sa.Column("name", sa.String(64), nullable=False),
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

    game_status = sa.Enum("scheduled", "in_progress", "final", name="game_status")
    game_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "games",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("game_date", sa.Date, nullable=False),
        sa.Column("home_team_id", sa.Integer, sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("away_team_id", sa.Integer, sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("home_score", sa.Integer),
        sa.Column("away_score", sa.Integer),
        sa.Column("status", game_status, nullable=False, server_default="scheduled"),
    )
    op.create_index("ix_games_game_date", "games", ["game_date"])
    op.create_index("ix_games_home_team_id", "games", ["home_team_id"])
    op.create_index("ix_games_away_team_id", "games", ["away_team_id"])

    op.create_table(
        "player_game_stats",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("game_id", sa.Integer, sa.ForeignKey("games.id"), nullable=False),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("players.id"), nullable=False),
        sa.Column("team_id", sa.Integer, sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("minutes", sa.Float),
        sa.Column("points", sa.Integer),
        sa.Column("rebounds", sa.Integer),
        sa.Column("assists", sa.Integer),
        sa.Column("steals", sa.Integer),
        sa.Column("blocks", sa.Integer),
        sa.Column("turnovers", sa.Integer),
        sa.Column("fg_made", sa.Integer),
        sa.Column("fg_attempted", sa.Integer),
        sa.Column("three_made", sa.Integer),
        sa.Column("three_attempted", sa.Integer),
        sa.Column("ft_made", sa.Integer),
        sa.Column("ft_attempted", sa.Integer),
        sa.Column("is_home", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("game_id", "player_id", name="uq_pgs_game_player"),
    )
    op.create_index("ix_pgs_game_id", "player_game_stats", ["game_id"])
    op.create_index("ix_pgs_player_id", "player_game_stats", ["player_id"])
    op.create_index("ix_pgs_team_id", "player_game_stats", ["team_id"])

    op.create_table(
        "team_defensive_ratings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("team_id", sa.Integer, sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("defensive_rating", sa.Float, nullable=False),
        sa.Column("pace", sa.Float),
        sa.UniqueConstraint("team_id", "date", name="uq_tdr_team_date"),
    )
    op.create_index("ix_tdr_team_id", "team_defensive_ratings", ["team_id"])
    op.create_index("ix_tdr_date", "team_defensive_ratings", ["date"])


def downgrade() -> None:
    op.drop_table("team_defensive_ratings")
    op.drop_table("player_game_stats")
    op.drop_table("games")
    op.execute("DROP TYPE IF EXISTS game_status")
    op.drop_table("players")
    op.drop_table("teams")
