"""add chunk_progress table (resumable cursor pagination)

Bookmarks pagination per (config, date-chunk) so a date range fills across several runs
instead of re-collecting the same head every time.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-07-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunk_progress",
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("last_cursor", sa.Text(), nullable=True),
        sa.Column("collected_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("has_next", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["parsing_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("config_id", "date_from", "date_to"),
    )


def downgrade() -> None:
    op.drop_table("chunk_progress")
