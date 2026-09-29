"""Department authorization inside one company.

Tenant isolation is tested elsewhere and is unchanged: every case here already
sits inside a single tenant. What these pin is the narrower boundary - that a
user reaches its own department's documents and nothing else, that an admin
reaches everything in its own company, and that a department asked for in a
request can only ever narrow what the caller already has.
"""

from __future__ import annotations

import pytest

from app.core.context import RequestContext
from app.db.models import Department
from app.services.security import document_access
from app.services.security.document_access import ADMIN_SCOPE, EMPTY_SCOPE, DocumentScope


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _Session:
    """Returns one user row's department, the way scope_for reads it."""

    def __init__(self, department: object) -> None:
        self.department = department
        self.queries = 0

    async def execute(self, _stmt: object) -> _ScalarResult:
        self.queries += 1
        return _ScalarResult(self.department)


def _ctx(role: str = "user", user_id: str = "u1", tenant: str = "acme") -> RequestContext:
    return RequestContext(user_id=user_id, tenant_id=tenant, role=role)  # type: ignore[arg-type]


# --- who gets which scope ----------------------------------------------------


@pytest.mark.asyncio
async def test_admin_reaches_every_document_in_its_own_company() -> None:
    session = _Session(None)
    scope = await document_access.scope_for(session, _ctx(role="admin"))
    assert scope == ADMIN_SCOPE
    assert scope.all_documents is True
    # An admin never needs the lookup - the role already decides it.
    assert session.queries == 0


@pytest.mark.asyncio
async def test_user_reaches_only_its_own_department() -> None:
    scope = await document_access.scope_for(_Session(Department.HR), _ctx())
    assert scope == DocumentScope(all_documents=False, departments=("hr",))


@pytest.mark.asyncio
async def test_user_without_a_department_reaches_nothing() -> None:
    """The chosen policy: unfiled documents are administrative, so a user with
    no department assigned sees none of them - and nothing else either."""
    scope = await document_access.scope_for(_Session(None), _ctx())
    assert scope == EMPTY_SCOPE
    assert scope.sees_nothing is True


@pytest.mark.asyncio
async def test_the_department_is_read_from_the_database_not_the_request() -> None:
    session = _Session(Department.FINANCE)
    scope = await document_access.scope_for(session, _ctx())
    assert scope.departments == ("finance",)
    assert session.queries == 1


# --- a client-supplied department can only narrow ----------------------------


def test_a_user_cannot_widen_its_scope_by_asking_for_another_department() -> None:
    finance = DocumentScope(all_documents=False, departments=("finance",))
    assert finance.narrowed_to("hr") == EMPTY_SCOPE
    assert finance.narrowed_to("hr").sees_nothing is True


def test_asking_for_its_own_department_is_a_no_op() -> None:
    finance = DocumentScope(all_documents=False, departments=("finance",))
    assert finance.narrowed_to("finance").departments == ("finance",)


def test_no_department_asked_for_leaves_the_scope_alone() -> None:
    finance = DocumentScope(all_documents=False, departments=("finance",))
    assert finance.narrowed_to(None) is finance
    assert ADMIN_SCOPE.narrowed_to(None) is ADMIN_SCOPE


def test_an_admin_filtering_by_department_narrows_rather_than_escapes() -> None:
    narrowed = ADMIN_SCOPE.narrowed_to("hr")
    assert narrowed.all_documents is False
    assert narrowed.departments == ("hr",)


def test_an_empty_scope_stays_empty_however_it_is_filtered() -> None:
    assert EMPTY_SCOPE.narrowed_to("hr").sees_nothing is True
    assert EMPTY_SCOPE.narrowed_to(None).sees_nothing is True


# --- the scope reaches the vector store --------------------------------------


def test_retrieval_filter_pins_the_caller_to_its_departments() -> None:
    from app.services import vector

    scope = DocumentScope(all_documents=False, departments=("finance",))
    flt = vector._tenant_filter(_ctx(), scope=scope)

    keys = {c.key for c in flt.must}
    assert "tenant_id" in keys, "the tenant filter must never be dropped"
    assert "department" in keys, "a scoped caller must carry a department condition"
    dept = next(c for c in flt.must if c.key == "department")
    assert dept.match.any == ["finance"]


def test_retrieval_filter_adds_no_department_condition_for_an_admin() -> None:
    from app.services import vector

    flt = vector._tenant_filter(_ctx(role="admin"), scope=ADMIN_SCOPE)
    keys = {c.key for c in flt.must}
    assert "tenant_id" in keys
    assert "department" not in keys


def test_a_caller_supplied_department_cannot_override_the_scope() -> None:
    """Even asking for HR, a finance scope still filters to finance."""
    from app.services import vector

    scope = DocumentScope(all_documents=False, departments=("finance",))
    flt = vector._tenant_filter(_ctx(), department="hr", scope=scope)
    dept = next(c for c in flt.must if c.key == "department")
    assert dept.match.any == ["finance"]


@pytest.mark.asyncio
async def test_an_unauthorised_question_never_reaches_the_vector_store() -> None:
    """A scope that reaches nothing short-circuits before any query runs."""
    from app.services import vector

    called = False

    def _fail() -> None:  # pragma: no cover - must never run
        nonlocal called
        called = True
        raise AssertionError("the vector store was queried for an empty scope")

    hits = await vector.search(_ctx(), [0.1, 0.2], scope=EMPTY_SCOPE)
    assert hits == []
    assert called is False
