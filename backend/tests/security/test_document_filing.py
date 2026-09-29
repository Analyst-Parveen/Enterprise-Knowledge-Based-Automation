"""Filing a document under a department is an admin action.

Which department a document sits in decides who can reach it, so changing it is
authorization, not metadata editing. A user must not be able to move a document
into a department it belongs to, nor out of one it does not.

These also cover the case that prompted the endpoint: a document uploaded
before departments existed, or uploaded with the form left on "Unassigned",
stays administrative until an admin files it - and filing it is what makes it
reachable, rather than loosening the rule for everybody.
"""

from __future__ import annotations

import pytest

from app.core.context import RequestContext
from app.core.exceptions import AuthorizationError
from app.db import repositories as repo
from app.db.models import Department, Document, DocumentStatus


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value

    def first(self) -> object:
        return self._value


class _Session:
    """Returns one document and records what was written."""

    def __init__(self, doc: Document | None) -> None:
        self.doc = doc
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def execute(self, statement: object) -> _Result:
        entity = statement.column_descriptions[0]["entity"]  # type: ignore[attr-defined]
        if entity is Document:
            return _Result(self.doc)
        return _Result(None)


def _doc(department: Department | None = None) -> Document:
    return Document(
        id="doc-1",
        tenant_id="acme",
        owner_id="u-owner",
        created_by="u-owner",
        name="Salary bands.pdf",
        original_filename="Salary bands.pdf",
        content_type="application/pdf",
        department=department,
        status=DocumentStatus.READY,
        source_uri="s3://b/acme/doc-1",
        size_bytes=10,
    )


def _ctx(role: str, user_id: str = "u1") -> RequestContext:
    return RequestContext(user_id=user_id, tenant_id="acme", role=role)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_an_admin_can_file_an_unassigned_document() -> None:
    doc = _doc(None)
    session = _Session(doc)

    updated = await repo.set_document_department(session, _ctx("admin"), "doc-1", Department.HR)

    assert updated.department is Department.HR


@pytest.mark.asyncio
async def test_filing_is_audited_with_both_the_old_and_the_new_department() -> None:
    doc = _doc(Department.FINANCE)
    session = _Session(doc)

    await repo.set_document_department(session, _ctx("admin"), "doc-1", Department.HR)

    events = [a for a in session.added if getattr(a, "event_type", None)]
    assert events, "filing a document must leave an audit trail"
    assert events[-1].event_type == "document.department_changed"


@pytest.mark.asyncio
async def test_a_plain_user_cannot_file_a_document() -> None:
    doc = _doc(None)
    session = _Session(doc)

    with pytest.raises(AuthorizationError):
        await repo.set_document_department(session, _ctx("user"), "doc-1", Department.HR)

    assert doc.department is None, "the document must not have moved"


@pytest.mark.asyncio
async def test_a_user_cannot_pull_a_document_into_its_own_department() -> None:
    """The attack the admin check exists for: an HR user filing a finance
    document as HR to make it readable."""
    doc = _doc(Department.FINANCE)
    session = _Session(doc)

    with pytest.raises(AuthorizationError):
        await repo.set_document_department(session, _ctx("user"), "doc-1", Department.HR)

    assert doc.department is Department.FINANCE


@pytest.mark.asyncio
async def test_a_refused_attempt_is_audited_as_an_authorization_failure() -> None:
    session = _Session(_doc(None))

    with pytest.raises(AuthorizationError):
        await repo.set_document_department(session, _ctx("user"), "doc-1", Department.HR)

    events = [a for a in session.added if getattr(a, "event_type", None)]
    assert any(e.event_type == "authz.unauthorized_document_filing" for e in events)


@pytest.mark.asyncio
async def test_an_admin_can_unfile_a_document_back_to_administrative() -> None:
    doc = _doc(Department.HR)
    session = _Session(doc)

    updated = await repo.set_document_department(session, _ctx("admin"), "doc-1", None)

    assert updated.department is None
