"""add reach and reach_breakdown to ads

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-18 03:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "b2c3d4e5f6a7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads", sa.Column("reach", sa.Integer(), nullable=True))
    op.add_column("ads", sa.Column("reach_breakdown", JSONB(), nullable=True))
    op.create_index("ix_ads_reach", "ads", ["reach"])


def downgrade() -> None:
    op.drop_index("ix_ads_reach", table_name="ads")
    op.drop_column("ads", "reach_breakdown")
    op.drop_column("ads", "reach")
