"""add_pf_per_game

Revision ID: a1f4c2d8e001
Revises: be9c7772bbc7
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f4c2d8e001'
down_revision: Union[str, None] = 'be9c7772bbc7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('player_season_stats', sa.Column('pf_per_game', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('player_season_stats', 'pf_per_game')
