"""Roster snapshots foundation (Step 3a): data_fetches, player_bio, roster_snapshots, roster_assignments.

Additive only. Downgrade drops everything created here. A sealed snapshot (and its
assignments) cannot be changed or deleted; sealing itself is the one allowed update.

Revision ID: m3a1_roster_snapshots
Revises: m1a1_myleague
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "m3a1_roster_snapshots"
down_revision: Union[str, None] = "m1a1_myleague"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_fetches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("endpoint", sa.String(64), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("store_path", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
    )
    op.create_table(
        "player_bio",
        sa.Column("player_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("birth_date", sa.Date()),
        sa.Column("height_in", sa.Float()),
        sa.Column("weight_lb", sa.Integer()),
        sa.Column("school", sa.String(128)),
        sa.Column("first_nba_season", sa.String(8)),
        sa.Column("draft_year", sa.Integer()),
        sa.Column("draft_round", sa.Integer()),
        sa.Column("draft_pick", sa.Integer()),
        sa.Column("source_fetch_id", sa.Integer(), sa.ForeignKey("data_fetches.id")),
    )
    op.create_table(
        "roster_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("season", sa.String(8), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("source_fetch_ids", sa.JSON(), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("roster_snapshots.id")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("sealed_at", sa.DateTime()),
        sa.CheckConstraint("kind IN ('live_pull','from_stats')", name="ck_roster_snapshot_kind"),
    )
    op.create_table(
        "roster_assignments",
        sa.Column("snapshot_id", sa.Integer(),
                  sa.ForeignKey("roster_snapshots.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("player_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), index=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("position", sa.String(8)),
        sa.Column("jersey", sa.String(4)),
        sa.CheckConstraint("status IN ('standard','two_way','exhibit10','inactive','free_agent')",
                           name="ck_roster_assignment_status"),
        sa.CheckConstraint("team_id IS NOT NULL OR status IN ('free_agent','inactive')",
                           name="ck_roster_assignment_team"),
    )

    # Immutability: once sealed_at is set, the snapshot row and its assignments are frozen.
    op.execute("""
    CREATE FUNCTION roster_snapshot_guard() RETURNS trigger AS $$
    BEGIN
        IF OLD.sealed_at IS NOT NULL THEN
            RAISE EXCEPTION 'roster snapshot % is sealed', OLD.id;
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql;
    CREATE TRIGGER trg_roster_snapshot_guard BEFORE UPDATE OR DELETE ON roster_snapshots
        FOR EACH ROW EXECUTE FUNCTION roster_snapshot_guard();

    CREATE FUNCTION roster_assignment_guard() RETURNS trigger AS $$
    DECLARE sid integer;
    BEGIN
        IF TG_OP = 'DELETE' THEN sid := OLD.snapshot_id; ELSE sid := NEW.snapshot_id; END IF;
        IF EXISTS (SELECT 1 FROM roster_snapshots WHERE id = sid AND sealed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'roster snapshot % is sealed', sid;
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql;
    CREATE TRIGGER trg_roster_assignment_guard BEFORE INSERT OR UPDATE OR DELETE ON roster_assignments
        FOR EACH ROW EXECUTE FUNCTION roster_assignment_guard();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_roster_assignment_guard ON roster_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_roster_snapshot_guard ON roster_snapshots")
    op.execute("DROP FUNCTION IF EXISTS roster_assignment_guard()")
    op.execute("DROP FUNCTION IF EXISTS roster_snapshot_guard()")
    op.drop_table("roster_assignments")
    op.drop_table("roster_snapshots")
    op.drop_table("player_bio")
    op.drop_table("data_fetches")
