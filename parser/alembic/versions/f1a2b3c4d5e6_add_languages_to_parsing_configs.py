"""add languages to parsing_configs

Revision ID: f1a2b3c4d5e6
Revises: e9f0a1b2c3d4
Create Date: 2026-06-13 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


revision = "f1a2b3c4d5e6"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='parsing_configs'"
    ))}
    if "languages" not in cols:
        op.add_column(
            "parsing_configs",
            sa.Column("languages", ARRAY(sa.String()), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("parsing_configs", "languages")
