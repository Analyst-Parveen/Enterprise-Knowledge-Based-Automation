"""Which of a company's documents one principal may reach.

Tenant isolation is decided before this module and never by it: every caller
here is already inside a single tenant, and that filter stays mandatory and
untouched. This layer answers the narrower question of who, inside that one
company, may see which documents.

The answer is a :class:`DocumentScope` - a small value object the database and
the vector store both apply. Keeping it in one place is what lets retrieval,
listing, download and chat share exactly one rule instead of drifting apart,
and it is the seam where future access types (explicit shares, teams,
per-document grants) can be added without touching the RAG pipeline again.

Today's rule:

* an admin sees every document in its own company
* a user sees the documents of the department it is assigned to
* documents with no department are administrative until someone files them, so
  only an admin sees them
* a user with no department assigned therefore sees nothing until an admin
  assigns one

The scope is always derived from the authenticated principal and the database.
A department supplied by the client can only ever narrow it further - it can
never widen it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.db.models import Department, User


@dataclass(frozen=True, slots=True)
class DocumentScope:
    """What one principal may reach inside its own tenant."""

    all_documents: bool
    departments: tuple[str, ...]

    @property
    def sees_nothing(self) -> bool:
        """A user with no department reaches no documents at all."""
        return not self.all_documents and not self.departments

    def narrowed_to(self, department: str | None) -> DocumentScope:
        """Apply a client-supplied department filter.

        Narrowing only. Asking for a department outside the scope yields an
        empty scope rather than access to it.
        """
        if department is None:
            return self
        if self.all_documents or department in self.departments:
            return DocumentScope(all_documents=False, departments=(department,))
        return DocumentScope(all_documents=False, departments=())


ADMIN_SCOPE = DocumentScope(all_documents=True, departments=())
EMPTY_SCOPE = DocumentScope(all_documents=False, departments=())


async def upload_department(
    session: AsyncSession,
    ctx: RequestContext,
    requested: Department | None,
) -> Department | None:
    """Which department an upload is filed under.

    An admin files documents wherever it chooses - that is the whole point of
    the control. Anyone else uploads into the department they are in right now,
    and the value on the request is ignored rather than rejected: a member has
    no way to name a department at all, so there is nothing to forge.

    Filing a member's upload under their own department is also what stops the
    surprise of uploading a document and immediately losing sight of it, which
    is what an unfiled upload would mean under the admin-only rule for
    documents with no department.
    """
    if ctx.is_admin:
        return requested

    scope = await scope_for(session, ctx)
    if scope.sees_nothing:
        # No department assigned: leave it unfiled for an admin to place,
        # rather than inventing one.
        return None
    return Department(scope.departments[0])


async def scope_for(session: AsyncSession, ctx: RequestContext) -> DocumentScope:
    """Resolve the caller's document scope from the database.

    The department is read from the user row, never from the request, so a
    forged or guessed value cannot widen what the caller reaches.
    """
    if ctx.is_admin:
        return ADMIN_SCOPE

    # ctx.user_id is the token subject. For a Cognito session that is
    # ``cognito_sub``; for a local dev token it is the row's own id. Matching
    # only one of them silently resolves no user at all, which would hand every
    # ordinary member an empty scope and hide their own department's documents.
    # The tenant is still pinned, so a subject can only ever find its own row.
    department = (
        await session.execute(
            select(User.department).where(
                (User.cognito_sub == ctx.user_id) | (User.id == ctx.user_id),
                User.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()

    if department is None:
        return EMPTY_SCOPE
    value = department.value if hasattr(department, "value") else str(department)
    return DocumentScope(all_documents=False, departments=(value,))
