"""add eu_countries, used_in_ads_count, spend_estimate to ads

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads", sa.Column("eu_countries", ARRAY(sa.String()), nullable=True))
    op.add_column("ads", sa.Column("used_in_ads_count", sa.Integer(), nullable=True))
    op.add_column("ads", sa.Column("spend_estimate", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_ads_eu_countries_gin",
        "ads",
        ["eu_countries"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_ads_eu_countries_gin", table_name="ads")
    op.drop_column("ads", "spend_estimate")
    op.drop_column("ads", "used_in_ads_count")
    op.drop_column("ads", "eu_countries")
