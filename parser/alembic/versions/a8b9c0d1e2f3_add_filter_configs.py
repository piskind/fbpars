"""add filter config fields to parsing_configs

Revision ID: a8b9c0d1e2f3
Revises: d4e5f6a7b8c9
Create Date: 2026-06-23 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


revision = "a8b9c0d1e2f3"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make keyword nullable — filter/fanpage configs may have no keyword
    op.alter_column(
        "parsing_configs",
        "keyword",
        existing_type=sa.String(255),
        nullable=True,
    )

    op.add_column("parsing_configs", sa.Column(
        "config_type", sa.String(32), nullable=False, server_default="keyword"
    ))
    op.add_column("parsing_configs", sa.Column(
        "active_status", sa.String(16), nullable=True
    ))
    op.add_column("parsing_configs", sa.Column(
        "media_type_filter", sa.String(16), nullable=True
    ))
    op.add_column("parsing_configs", sa.Column(
        "platforms", ARRAY(sa.String()), nullable=True
    ))
    op.add_column("parsing_configs", sa.Column(
        "date_from", sa.Date(), nullable=True
    ))
    op.add_column("parsing_configs", sa.Column(
        "date_to", sa.Date(), nullable=True
    ))
    op.add_column("parsing_configs", sa.Column(
        "advertiser", sa.String(255), nullable=True
    ))
    op.add_column("parsing_configs", sa.Column(
        "auto_date_from_last_parse", sa.Boolean(), nullable=False, server_default="false"
    ))
    op.add_column("parsing_configs", sa.Column(
        "last_parsed_at", sa.DateTime(timezone=True), nullable=True
    ))


def downgrade() -> None:
    op.drop_column("parsing_configs", "last_parsed_at")
    op.drop_column("parsing_configs", "auto_date_from_last_parse")
    op.drop_column("parsing_configs", "advertiser")
    op.drop_column("parsing_configs", "date_to")
    op.drop_column("parsing_configs", "date_from")
    op.drop_column("parsing_configs", "platforms")
    op.drop_column("parsing_configs", "media_type_filter")
    op.drop_column("parsing_configs", "active_status")
    op.drop_column("parsing_configs", "config_type")
    op.alter_column(
        "parsing_configs",
        "keyword",
        existing_type=sa.String(255),
        nullable=False,
    )
