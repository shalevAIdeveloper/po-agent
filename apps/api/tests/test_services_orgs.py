"""Service-level mapping of DB errors and transaction retries, without a database."""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from app import db
from app.auth.jwt import VerifiedToken
from app.services import orgs as orgs_service

USER = VerifiedToken(user_id=uuid4(), claims={"sub": "x", "role": "authenticated"})


class _PgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("driver message with ACME 123 Main St")
        self.sqlstate = sqlstate


def _db_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("select ...", None, _PgError(sqlstate))


def test_sqlstate_is_read_from_the_driver_error() -> None:
    assert db.sqlstate(_db_error("23505")) == "23505"
    assert db.sqlstate(ValueError("no orig")) is None


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [
        ("23505", orgs_service.SlugUnavailableError),
        ("53400", orgs_service.OrgLimitReachedError),
        ("23514", orgs_service.InvalidOrganizationError),
    ],
)
async def test_create_maps_known_sqlstates(
    monkeypatch: pytest.MonkeyPatch, sqlstate: str, expected: type[Exception]
) -> None:
    async def run_as_user(_claims: Mapping[str, Any], _work: Callable[..., Awaitable[Any]]) -> Any:
        raise _db_error(sqlstate)

    monkeypatch.setattr(orgs_service, "run_as_user", run_as_user)

    with pytest.raises(expected) as excinfo:
        await orgs_service.create_organization(USER, "Acme", "acme-supply")

    # The client-facing error never carries the driver message.
    assert "ACME" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


async def test_create_reraises_unknown_db_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_as_user(_claims: Mapping[str, Any], _work: Callable[..., Awaitable[Any]]) -> Any:
        raise _db_error("XX000")

    monkeypatch.setattr(orgs_service, "run_as_user", run_as_user)

    with pytest.raises(DBAPIError):
        await orgs_service.create_organization(USER, "Acme", "acme-supply")


@pytest.fixture
def fake_transactions(monkeypatch: pytest.MonkeyPatch) -> list[Mapping[str, Any]]:
    opened: list[Mapping[str, Any]] = []

    @asynccontextmanager
    async def rls_transaction(claims: Mapping[str, Any]) -> AsyncIterator[Any]:
        opened.append(claims)
        yield object()

    monkeypatch.setattr(db, "rls_transaction", rls_transaction)
    return opened


@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
async def test_run_as_user_retries_serialization_failures_and_deadlocks(
    fake_transactions: list[Mapping[str, Any]], sqlstate: str
) -> None:
    attempts = 0

    async def work(_session: Any) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _db_error(sqlstate)
        return "done"

    assert await db.run_as_user(USER.claims, work) == "done"
    assert attempts == 2
    assert len(fake_transactions) == 2


async def test_run_as_user_gives_up_after_max_attempts(
    fake_transactions: list[Mapping[str, Any]],
) -> None:
    async def work(_session: Any) -> str:
        raise _db_error("40001")

    with pytest.raises(DBAPIError):
        await db.run_as_user(USER.claims, work)
    assert len(fake_transactions) == db.MAX_TRANSACTION_ATTEMPTS


async def test_run_as_user_does_not_retry_other_errors(
    fake_transactions: list[Mapping[str, Any]],
) -> None:
    async def work(_session: Any) -> str:
        raise _db_error("23505")

    with pytest.raises(DBAPIError):
        await db.run_as_user(USER.claims, work)
    assert len(fake_transactions) == 1


async def test_rename_maps_check_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_as_user(_claims: Mapping[str, Any], _work: Callable[..., Awaitable[Any]]) -> Any:
        raise _db_error("23514")

    monkeypatch.setattr(orgs_service, "run_as_user", run_as_user)

    with pytest.raises(orgs_service.InvalidOrganizationError):
        await orgs_service.rename_organization(USER, uuid4(), "x")


def test_only_allow_listed_claims_reach_postgres() -> None:
    claims = {
        "sub": "u",
        "role": "authenticated",
        "is_anonymous": False,
        "app_metadata": {"provider": "email"},
        "user_metadata": {"role": "owner", "org_id": "anything"},
        "email": "a@b.c",
        "custom": "x",
    }

    forwarded = db.rls_claims(claims)

    assert forwarded == {
        "sub": "u",
        "role": "authenticated",
        "is_anonymous": False,
        "app_metadata": {"provider": "email"},
    }


@pytest.mark.parametrize("sqlstate", ["57014", "55P03"])
async def test_timeouts_become_busy_and_are_not_retried(
    fake_transactions: list[Mapping[str, Any]], sqlstate: str
) -> None:
    async def work(_session: Any) -> str:
        raise _db_error(sqlstate)

    with pytest.raises(db.DatabaseBusyError) as excinfo:
        await db.run_as_user(USER.claims, work)
    assert len(fake_transactions) == 1
    assert excinfo.value.__cause__ is None
