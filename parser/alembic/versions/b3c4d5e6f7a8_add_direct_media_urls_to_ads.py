"""add direct FB CDN media url arrays to ads

Store image/video/poster URLs straight from FB (no S3 download) so the client
loads media directly from fbcdn.net. See ENABLE_MEDIA_DOWNLOAD.

Revision ID: b3c4d5e6f7a8
Revises: a8b9c0d1e2f3
Create Date: 2026-07-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


revision = "b3c4d5e6f7a8"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads", sa.Column("image_urls", ARRAY(sa.Text()), nullable=True))
    op.add_column("ads", sa.Column("video_urls", ARRAY(sa.Text()), nullable=True))
    op.add_column("ads", sa.Column("poster_urls", ARRAY(sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("ads", "poster_urls")
    op.drop_column("ads", "video_urls")
    op.drop_column("ads", "image_urls")
