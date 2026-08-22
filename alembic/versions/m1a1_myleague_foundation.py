"""MyLeague foundation: scope='myleague', myleague_state, myleague_event (M-1a.1).

Adds a stateful franchise-mode layer alongside the existing league batch
runner. See project-next-session-focus for the M-1a design lock.

Revision ID: m1a1_myleague
Revises: c1a1_sim_run_scope
Create Date: 2026-08-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "m1a1_myleague"
down_revision: Union[str, None] = "c1a1_sim_run_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Widen the scope CHECK to allow 'myleague'. For myleague scope,
    # simulation_runs.team_id stays NULL — the controlled team lives on the
    # sibling myleague_state row (nullable to leave God-mode door open).
    op.drop_constraint("ck_sim_run_scope_team_id", "simulation_runs", type_="check")
    op.create_check_constraint(
        "ck_sim_run_scope_team_id",
        "simulation_runs",
        "(scope = 'team' AND team_id IS NOT NULL) OR "
        "(scope = 'league' AND team_id IS NULL) OR "
        "(scope = 'myleague' AND team_id IS NULL)",
    )

    # 2. MyLeagueState — 1:1 sibling of SimulationRun for scope='myleague'.
    op.create_table(
        "myleague_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "simulation_id", sa.Integer(),
            sa.ForeignKey("simulation_runs.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        # Nullable so God-mode is a future config change, not a schema change.
        sa.Column(
            "controlled_team_id", sa.Integer(),
            sa.ForeignKey("teams.id"), nullable=True, index=True,
        ),
        # Schedule cursor — monotonically non-decreasing; enforced at write time.
        sa.Column("current_calendar_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # 3. MyLeagueEvent — the mutation log. Event-sourced; state at time T is
    # fold(events with applied_at_date <= T, base_state). See design lock.
    op.create_table(
        "myleague_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "myleague_state_id", sa.Integer(),
            sa.ForeignKey("myleague_state.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        # M-1a event types: SET_UNAVAILABLE, SET_AVAILABLE. Widened later for
        # INJURY / TRADE / MPG_OVERRIDE — kept as free-form String for now to
        # avoid a schema change per event type.
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        # applied_at_date: the in-universe date the event takes effect.
        # Semantics (locked with Xavier): affects games on this date that have
        # not yet been simulated + all subsequent games until superseded.
        # Retroactive events (applied_at_date <= any completed game.game_date)
        # are rejected at write time to preserve historical immutability.
        sa.Column("applied_at_date", sa.Date(), nullable=False, index=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("myleague_events")
    op.drop_table("myleague_state")
    op.drop_constraint("ck_sim_run_scope_team_id", "simulation_runs", type_="check")
    op.create_check_constraint(
        "ck_sim_run_scope_team_id",
        "simulation_runs",
        "(scope = 'team' AND team_id IS NOT NULL) OR "
        "(scope = 'league' AND team_id IS NULL)",
    )
