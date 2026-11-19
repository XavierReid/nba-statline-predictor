"""Add player_bio.full_name (Step 3a): snapshots must show who rookies/new signees are.

Revision ID: m3a2_bio_full_name
Revises: m3a1_roster_snapshots
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "m3a2_bio_full_name"
down_revision: Union[str, None] = "m3a1_roster_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("player_bio", sa.Column("full_name", sa.String(128)))


def downgrade() -> None:
    op.drop_column("player_bio", "full_name")
