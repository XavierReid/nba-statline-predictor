"""add_team_season_stats

Revision ID: e1580915be22
Revises: aa432dca8ec4
Create Date: 2026-06-26 15:42:29.980477

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1580915be22'
down_revision: Union[str, None] = 'aa432dca8ec4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_season_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.String(10), nullable=False),
        sa.Column("pace", sa.Float(), nullable=False),
        sa.Column("off_rating", sa.Float(), nullable=False),
        sa.Column("def_rating", sa.Float(), nullable=False),
        sa.Column("net_rating", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "season", name="uq_team_season_stats"),
    )


def downgrade() -> None:
    op.drop_table("team_season_stats")
