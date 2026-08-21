"""SimulationRun scope enum + nullable team_id for league sims (C-1.1).

Revision ID: c1a1_sim_run_scope
Revises: c3d7a1b9f2e4
Create Date: 2026-08-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1a1_sim_run_scope"
down_revision: Union[str, None] = "c3d7a1b9f2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add scope column with 'team' default so existing rows are backfilled.
    op.add_column(
        "simulation_runs",
        sa.Column("scope", sa.String(length=8), nullable=False, server_default="team"),
    )
    # 2. Make team_id nullable (was NOT NULL).
    op.alter_column(
        "simulation_runs", "team_id",
        existing_type=sa.Integer(), nullable=True,
    )
    # 3. CHECK: scope='team' ⇒ team_id NOT NULL; scope='league' ⇒ team_id IS NULL.
    op.create_check_constraint(
        "ck_sim_run_scope_team_id",
        "simulation_runs",
        "(scope = 'team' AND team_id IS NOT NULL) OR "
        "(scope = 'league' AND team_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sim_run_scope_team_id", "simulation_runs", type_="check")
    op.alter_column(
        "simulation_runs", "team_id",
        existing_type=sa.Integer(), nullable=False,
    )
    op.drop_column("simulation_runs", "scope")
