"""Department access across the whole document lifecycle.

These cover two bugs that shipped together and hid each other:

* the caller's row was looked up by ``User.id`` while the token subject is the
  Cognito ``sub``, so no ordinary member ever resolved a department and every
  one of them saw an empty workspace - including the documents they had just
  uploaded themselves;
* an upload took its department from the form, so a member's upload landed
  unfiled and, under the admin-only rule for unfiled documents, vanished from
  the uploader's own view.

They also pin the behaviour an admin depends on: moving a user between
departments changes what that user reaches on the very next request, with no
new sign-in and without touching a single document.
"""

from __future__ import annotations

import pytest

from app.core.context import RequestContext
from app.db.models import Department, User
from app.services import vector
from app.services.security import document_access
from app.services.security.document_access import ADMIN_SCOPE, EMPTY_SCOPE


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _Directory:
    """One company's user rows, queried the way the real session is.

    Matches on either identifier, so a test can prove the lookup works for a
    Cognito subject as well as a local id.
    """

    def __init__(self, users: list[User]) -> None:
        self.users = users

    async def execute(self, statement: object) -> _Result:
        compiled = statement.compile()  # type: ignore[attr-defined]
        sql = str(compiled)
        params = compiled.params

        # Only the columns the query actually names are searched. A query that
        # matches on users.id alone therefore finds nothing for a Cognito
        # subject - which is exactly the bug these tests exist to catch, so the
        # fake must not paper over it.
        fields: list[str] = []
        if "cognito_sub" in sql:
            fields.append("cognito_sub")
        if "users.id" in sql:
            fields.append("id")

        subject = next(
            (v for k, v in params.items() if "cognito_sub" in k or k.startswith("id_")), None
        )
        if subject is None:
            subject = next((v for k, v in params.items() if k.startswith("id")), None)
        tenant = next((v for k, v in params.items() if "tenant_id" in k), None)

        for user in self.users:
            if user.tenant_id != tenant:
                continue
            if any(getattr(user, field) == subject for field in fields):
                return _Result(user.department)
        return _Result(None)


def _user(
    *,
    uid: str,
    sub: str | None,
    department: Department | None,
    tenant: str = "acme",
) -> User:
    return User(
        id=uid, cognito_sub=sub, tenant_id=tenant, email=f"{uid}@acme.test", department=department
    )


def _ctx(subject: str, role: str = "user", tenant: str = "acme") -> RequestContext:
    return RequestContext(user_id=subject, tenant_id=tenant, role=role)  # type: ignore[arg-type]


HR_USER = _user(uid="u-hr", sub="cognito-hr", department=Department.HR)
FIN_USER = _user(uid="u-fin", sub="cognito-fin", department=Department.FINANCE)
NO_DEPT = _user(uid="u-none", sub="cognito-none", department=None)
OTHER_CO = _user(uid="u-other", sub="cognito-other", department=Department.HR, tenant="globex")
DIRECTORY = _Directory([HR_USER, FIN_USER, NO_DEPT, OTHER_CO])


# --- the lookup bug ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cognito_subject_resolves_its_department() -> None:
    """The bug: the token carries the Cognito sub, the row is keyed by its own
    id, and matching only the id left every member with an empty scope."""
    scope = await document_access.scope_for(DIRECTORY, _ctx("cognito-hr"))
    assert scope.departments == ("hr",)
    assert scope.sees_nothing is False


@pytest.mark.asyncio
async def test_a_local_subject_still_resolves_its_department() -> None:
    """A dev token's subject is the row's own id; both forms must work."""
    scope = await document_access.scope_for(DIRECTORY, _ctx("u-hr"))
    assert scope.departments == ("hr",)


@pytest.mark.asyncio
async def test_a_subject_from_another_company_resolves_nothing() -> None:
    """Tenant stays pinned, so a subject cannot reach across companies."""
    scope = await document_access.scope_for(DIRECTORY, _ctx("cognito-other", tenant="acme"))
    assert scope == EMPTY_SCOPE


@pytest.mark.asyncio
async def test_an_unknown_subject_reaches_nothing() -> None:
    assert await document_access.scope_for(DIRECTORY, _ctx("nobody")) == EMPTY_SCOPE


# --- upload files under the uploader's own department ------------------------


@pytest.mark.asyncio
async def test_a_members_upload_is_filed_under_their_own_department() -> None:
    """CASE 1: the uploader must be able to see what they just uploaded."""
    filed = await document_access.upload_department(DIRECTORY, _ctx("cognito-hr"), None)
    assert filed is Department.HR


@pytest.mark.asyncio
async def test_a_member_cannot_file_an_upload_into_another_department() -> None:
    """The form said Finance; the HR member's upload still lands in HR."""
    filed = await document_access.upload_department(
        DIRECTORY, _ctx("cognito-hr"), Department.FINANCE
    )
    assert filed is Department.HR


@pytest.mark.asyncio
async def test_an_admin_files_an_upload_where_it_chooses() -> None:
    """CASE 2: an admin's HR upload really is filed as HR."""
    assert (
        await document_access.upload_department(DIRECTORY, _ctx("admin", "admin"), Department.HR)
        is Department.HR
    )
    assert await document_access.upload_department(DIRECTORY, _ctx("admin", "admin"), None) is None


@pytest.mark.asyncio
async def test_a_member_without_a_department_uploads_unfiled() -> None:
    """Nothing is invented for them; an admin files it later."""
    assert await document_access.upload_department(DIRECTORY, _ctx("cognito-none"), None) is None


# --- moving a user between departments ---------------------------------------


@pytest.mark.asyncio
async def test_moving_a_user_changes_what_they_reach_on_the_next_request() -> None:
    """CASE 3 and 4: HR -> Sales -> HR, with no new sign-in and no document
    touched. The scope is read from the row every time, so there is no stale
    token to invalidate."""
    mover = _user(uid="u-mv", sub="cognito-mv", department=Department.HR)
    directory = _Directory([mover])
    ctx = _ctx("cognito-mv")

    assert (await document_access.scope_for(directory, ctx)).departments == ("hr",)

    mover.department = Department.SALES
    scope = await document_access.scope_for(directory, ctx)
    assert scope.departments == ("sales",)
    assert "hr" not in scope.departments, "HR access must be gone immediately"

    mover.department = Department.HR
    assert (await document_access.scope_for(directory, ctx)).departments == ("hr",)


# --- the same scope reaches retrieval ----------------------------------------


@pytest.mark.asyncio
async def test_retrieval_is_filtered_by_the_callers_current_department() -> None:
    scope = await document_access.scope_for(DIRECTORY, _ctx("cognito-fin"))
    flt = vector._tenant_filter(_ctx("cognito-fin"), scope=scope)

    keys = {c.key for c in flt.must}
    assert "tenant_id" in keys, "the tenant filter must never be dropped"
    dept = next(c for c in flt.must if c.key == "department")
    assert dept.match.any == ["finance"]


@pytest.mark.asyncio
async def test_a_member_cannot_widen_retrieval_with_a_request_parameter() -> None:
    """A finance member asking for HR gets finance, not HR."""
    scope = await document_access.scope_for(DIRECTORY, _ctx("cognito-fin"))
    flt = vector._tenant_filter(_ctx("cognito-fin"), department="hr", scope=scope.narrowed_to("hr"))
    # Asking outside the scope empties it rather than granting it.
    assert scope.narrowed_to("hr") == EMPTY_SCOPE
    dept = next(c for c in flt.must if c.key == "department")
    assert dept.match.any == []


@pytest.mark.asyncio
async def test_an_admin_retrieves_across_its_own_company_only() -> None:
    scope = await document_access.scope_for(DIRECTORY, _ctx("admin", "admin"))
    assert scope == ADMIN_SCOPE
    flt = vector._tenant_filter(_ctx("admin", "admin"), scope=scope)
    keys = {c.key for c in flt.must}
    assert "tenant_id" in keys
    assert "department" not in keys
