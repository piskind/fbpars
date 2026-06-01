"""add partner/category to parsing_configs and parser_runs table

Revision ID: e9f0a1b2c3d4
Revises: c7d8e9f0a1b2
Create Date: 2026-06-01 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "e9f0a1b2c3d4"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("parsing_configs", sa.Column("partner", sa.Text(), nullable=True))
    op.add_column("parsing_configs", sa.Column("category", sa.Text(), nullable=True))

    op.create_table(
        "parser_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="triggered"),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("log_tail", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("parser_runs")
    op.drop_column("parsing_configs", "category")
    op.drop_column("parsing_configs", "partner")
