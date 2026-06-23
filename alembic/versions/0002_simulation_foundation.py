"""simulation foundation: player season stats, attributes, tendencies, overrides

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "player_season_stats",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("players.id"), nullable=False, index=True),
        sa.Column("season", sa.String(8), nullable=False),
        sa.Column("team_id", sa.Integer, sa.ForeignKey("teams.id")),
        sa.Column("games_played", sa.Integer),
        sa.Column("minutes_per_game", sa.Float),
        sa.Column("points", sa.Float),
        sa.Column("rebounds", sa.Float),
        sa.Column("assists", sa.Float),
        sa.Column("steals", sa.Float),
        sa.Column("blocks", sa.Float),
        sa.Column("turnovers", sa.Float),
        sa.Column("fgm", sa.Float),
        sa.Column("fga", sa.Float),
        sa.Column("fg_pct", sa.Float),
        sa.Column("fg3m", sa.Float),
        sa.Column("fg3a", sa.Float),
        sa.Column("fg3_pct", sa.Float),
        sa.Column("ftm", sa.Float),
        sa.Column("fta", sa.Float),
        sa.Column("ft_pct", sa.Float),
        sa.Column("plus_minus", sa.Float),
        sa.UniqueConstraint("player_id", "season", name="uq_pss_player_season"),
    )

    op.create_table(
        "player_attributes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("players.id"), nullable=False, index=True),
        sa.Column("season", sa.String(8), nullable=False),
        sa.Column("three_point", sa.Integer, server_default="40"),
        sa.Column("free_throw", sa.Integer, server_default="40"),
        sa.Column("mid_range", sa.Integer, server_default="40"),
        sa.Column("close_shot", sa.Integer, server_default="50"),
        sa.Column("layup", sa.Integer, server_default="50"),
        sa.Column("dunk", sa.Integer, server_default="50"),
        sa.Column("passing", sa.Integer, server_default="50"),
        sa.Column("ball_handle", sa.Integer, server_default="50"),
        sa.Column("steal", sa.Integer, server_default="50"),
        sa.Column("block", sa.Integer, server_default="50"),
        sa.Column("perimeter_defense", sa.Integer, server_default="50"),
        sa.Column("interior_defense", sa.Integer, server_default="50"),
        sa.Column("speed", sa.Integer, server_default="50"),
        sa.Column("acceleration", sa.Integer, server_default="50"),
        sa.Column("strength", sa.Integer, server_default="50"),
        sa.Column("stamina", sa.Integer, server_default="50"),
        sa.Column("vertical", sa.Integer, server_default="50"),
        sa.Column("offensive_rebound", sa.Integer, server_default="50"),
        sa.Column("defensive_rebound", sa.Integer, server_default="50"),
        sa.Column("overall_rating", sa.Integer, server_default="50"),
        sa.UniqueConstraint("player_id", "season", name="uq_pa_player_season"),
    )

    op.create_table(
        "player_attribute_overrides",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("players.id"), nullable=False, index=True),
        sa.Column("season", sa.String(8), nullable=False),
        sa.Column("attribute", sa.String(32), nullable=False),
        sa.Column("value", sa.Integer, nullable=False),
        sa.Column("reason", sa.Text),
        sa.UniqueConstraint("player_id", "season", "attribute", name="uq_pao_player_season_attr"),
    )

    op.create_table(
        "player_tendencies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("players.id"), nullable=False, index=True),
        sa.Column("season", sa.String(8), nullable=False),
        sa.Column("usage_rate", sa.Float),
        sa.Column("shot_tendency", sa.Float),
        sa.Column("three_point_rate", sa.Float),
        sa.Column("assist_rate", sa.Float),
        sa.Column("rebound_rate", sa.Float),
        sa.Column("turnover_rate", sa.Float),
        sa.UniqueConstraint("player_id", "season", name="uq_pt_player_season"),
    )


def downgrade() -> None:
    op.drop_table("player_tendencies")
    op.drop_table("player_attribute_overrides")
    op.drop_table("player_attributes")
    op.drop_table("player_season_stats")
