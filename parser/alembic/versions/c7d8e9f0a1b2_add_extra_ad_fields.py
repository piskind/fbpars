"""add extra ad fields for MVP

Revision ID: c7d8e9f0a1b2
Revises: 60bc03c5e629
Create Date: 2026-05-23 14:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "c7d8e9f0a1b2"
down_revision = "60bc03c5e629"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads", sa.Column("lead_form", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("ads", sa.Column("language", sa.String(length=8), nullable=True))
    op.add_column("ads", sa.Column("app_store", sa.String(length=32), nullable=True))
    op.add_column("ads", sa.Column("ecom_platform", sa.String(length=64), nullable=True))
    op.add_column("ads", sa.Column("ip", sa.String(length=64), nullable=True))
    op.create_index("ix_ads_language", "ads", ["language"])
    op.create_index("ix_ads_app_store", "ads", ["app_store"])
    op.create_index("ix_ads_ecom_platform", "ads", ["ecom_platform"])


def downgrade() -> None:
    op.drop_index("ix_ads_ecom_platform", table_name="ads")
    op.drop_index("ix_ads_app_store", table_name="ads")
    op.drop_index("ix_ads_language", table_name="ads")
    op.drop_column("ads", "ip")
    op.drop_column("ads", "ecom_platform")
    op.drop_column("ads", "app_store")
    op.drop_column("ads", "language")
    op.drop_column("ads", "lead_form")
