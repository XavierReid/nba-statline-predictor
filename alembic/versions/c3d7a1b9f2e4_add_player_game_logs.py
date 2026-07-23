"""add player_game_logs

Revision ID: c3d7a1b9f2e4
Revises: a1f4c2d8e001
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d7a1b9f2e4'
down_revision: Union[str, None] = 'a1f4c2d8e001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('player_game_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('season', sa.String(length=7), nullable=False),
    sa.Column('game_id', sa.String(length=20), nullable=False),
    sa.Column('player_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('minutes', sa.Float(), nullable=False),
    sa.Column('pts', sa.Integer(), nullable=True),
    sa.Column('reb', sa.Integer(), nullable=True),
    sa.Column('ast', sa.Integer(), nullable=True),
    sa.Column('fgm', sa.Integer(), nullable=True),
    sa.Column('fga', sa.Integer(), nullable=True),
    sa.Column('fg3m', sa.Integer(), nullable=True),
    sa.Column('fta', sa.Integer(), nullable=True),
    sa.Column('ftm', sa.Integer(), nullable=True),
    sa.Column('tov', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_player_game_logs_season_game', 'player_game_logs', ['season', 'game_id'], unique=False)
    op.create_index('ix_player_game_logs_season_player', 'player_game_logs', ['season', 'player_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_player_game_logs_season_player', table_name='player_game_logs')
    op.drop_index('ix_player_game_logs_season_game', table_name='player_game_logs')
    op.drop_table('player_game_logs')
