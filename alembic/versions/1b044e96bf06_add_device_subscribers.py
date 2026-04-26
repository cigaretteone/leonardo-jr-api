"""add_device_subscribers

Revision ID: 1b044e96bf06
Revises: f5ed4d0936ca
Create Date: 2026-04-26 23:40:38.749686

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b044e96bf06'
down_revision: Union[str, Sequence[str], None] = 'f5ed4d0936ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_subscribers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("device_id", "channel", "target", name="uq_device_subscribers_device_channel_target"),
        sa.CheckConstraint("channel IN ('line', 'email', 'phone')", name="device_subscribers_channel_check"),
    )
    op.create_index(
        "idx_device_subscribers_device_enabled",
        "device_subscribers",
        ["device_id", "enabled"],
    )


def downgrade() -> None:
    op.drop_index("idx_device_subscribers_device_enabled", table_name="device_subscribers")
    op.drop_table("device_subscribers")
