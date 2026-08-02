"""Narrow the house-ad feature flag to its only eligible audience.

Revision ID: 0004_ad_inventory_contract
Revises: 0003_test_buy
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_ad_inventory_contract"
down_revision: str | None = "0003_test_buy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE feature_flags SET audience = 'free' "
            "WHERE key = 'ad_system' AND audience = 'all'"
        )
    )


def downgrade() -> None:
    # `free` was already valid before this revision, so there is no sound way to
    # distinguish a previously-free row from a normalized `all` row.
    pass
