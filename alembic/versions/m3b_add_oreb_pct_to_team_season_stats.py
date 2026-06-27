"""Add oreb_pct to team_season_stats

Revision ID: m3b_add_oreb_pct
Revises: be759481a4d9
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa

revision = 'm3b_add_oreb_pct'
down_revision = 'be759481a4d9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'team_season_stats',
        sa.Column('oreb_pct', sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('team_season_stats', 'oreb_pct')
