"""add vertical to configs and ads

Revision ID: 60bc03c5e629
Revises: 
Create Date: 2026-05-21

"""
from alembic import op
import sqlalchemy as sa


revision = "60bc03c5e629"
down_revision = "a5165d673c16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parsing_configs",
        sa.Column("vertical", sa.String(length=32), nullable=False, server_default="nutra"),
    )
    op.add_column(
        "ads",
        sa.Column("vertical", sa.String(length=32), nullable=True),
    )
    op.execute("UPDATE ads SET vertical='nutra'")
    op.create_index("ix_parsing_configs_vertical", "parsing_configs", ["vertical"])
    op.create_index("ix_ads_vertical", "ads", ["vertical"])


def downgrade() -> None:
    op.drop_index("ix_ads_vertical", table_name="ads")
    op.drop_index("ix_parsing_configs_vertical", table_name="parsing_configs")
    op.drop_column("ads", "vertical")
    op.drop_column("parsing_configs", "vertical")