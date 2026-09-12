"""Platform-level onboarding: the platform_admin role and tenant provenance.

Additive only. No existing column is altered or dropped, so tenant, user,
document and conversation data carried over from the previous session survives
this migration untouched.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kept in sync with app.core.context.PLATFORM_TENANT_ID.
PLATFORM_TENANT_ID = "platform"


def upgrade() -> None:
    # -- the platform role ------------------------------------------------
    # PostgreSQL 12+ permits ADD VALUE inside a transaction as long as the new
    # label is not *used* in the same transaction. Nothing below writes a
    # platform_admin row, so this is safe under Alembic's transactional DDL.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'PLATFORM_ADMIN'")

    # -- tenant provenance ------------------------------------------------
    op.add_column("tenants", sa.Column("contact_email", sa.String(320), nullable=True))
    op.add_column("tenants", sa.Column("created_by", sa.String(64), nullable=True))

    # -- who invited whom -------------------------------------------------
    op.add_column("users", sa.Column("invited_by", sa.String(64), nullable=True))

    # -- the reserved platform tenant -------------------------------------
    # Platform operators need a tenant row because users.tenant_id is a
    # non-nullable foreign key, and because keeping the platform inside the
    # same tenancy model means tenant filtering has no exceptions.
    op.execute(
        sa.text(
            """
            INSERT INTO tenants (id, name, slug, is_active, created_at, updated_at)
            VALUES (:id, :name, :slug, true, now(), now())
            ON CONFLICT (id) DO NOTHING
            """
        ).bindparams(
            id=PLATFORM_TENANT_ID,
            name="Platform Operations",
            slug=PLATFORM_TENANT_ID,
        )
    )


def downgrade() -> None:
    # The platform tenant is removed only when it holds no operators, so a
    # downgrade can never orphan or cascade-delete an identity.
    op.execute(
        sa.text("DELETE FROM tenants WHERE id = :id AND NOT EXISTS "
                "(SELECT 1 FROM users WHERE tenant_id = :id)").bindparams(
            id=PLATFORM_TENANT_ID
        )
    )
    op.drop_column("users", "invited_by")
    op.drop_column("tenants", "created_by")
    op.drop_column("tenants", "contact_email")
    # PostgreSQL cannot drop a single enum label. The value is left in place;
    # it is inert once no row references it.
