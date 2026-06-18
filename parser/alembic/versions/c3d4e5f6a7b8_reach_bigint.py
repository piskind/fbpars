"""change reach column to bigint

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-18 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("ads", "reach", type_=sa.BigInteger())


def downgrade() -> None:
    op.alter_column("ads", "reach", type_=sa.Integer())
