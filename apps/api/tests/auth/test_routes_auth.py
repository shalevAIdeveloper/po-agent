"""HTTP-level auth and org scoping with the DB-backed services stubbed out."""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt import KeysUnavailableError, VerifiedToken
from app.auth.org import OrgNotFoundError, OrgSelectionRequiredError, select_membership
from app.config import Settings
from app.db import DatabaseBusyError
from app.main import create_app
from app.schemas.orgs import MeResponse, Organization, Role
from app.services import orgs as orgs_service
from app.services.orgs import Membership, OrgLimitReachedError, SlugUnavailableError
from tests.auth.keys import ISSUER, StaticKeys, claims, make_es256_key, sign

KEY = make_es256_key()
USER_ID = uuid4()
ORG_A = UUID("00000000-0000-4000-8000-0000000000a1")
ORG_B = UUID("00000000-0000-4000-8000-0000000000b1")


def _org(org_id: UUID, role: Role = "owner") -> Organization:
    return Organization(
        id=org_id, name=f"Org {org_id.hex[-2:]}", slug=f"org-{org_id.hex[-2:]}", role=role
    )


@pytest.fixture
def memberships(monkeypatch: pytest.MonkeyPatch) -> list[Membership]:
    current: list[Membership] = []

    async def list_memberships(_user: VerifiedToken) -> list[Membership]:
        return current

    monkeypatch.setattr(orgs_service, "list_memberships", list_memberships)
    return current


@pytest.fixture
def client(memberships: list[Membership]) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, supabase_url="http://supabase.test")
    assert settings.jwt_issuer == ISSUER
    with TestClient(create_app(settings, jwks=StaticKeys.of(KEY))) as test_client:
        yield test_client


def _auth(user_id: UUID = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {sign(KEY, claims(user_id))}"}


# ----- authentication -----


@pytest.mark.parametrize("path", ["/me", "/orgs/current"])
def test_missing_token_is_401_with_challenge(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Not authenticated"}


def test_invalid_token_gets_the_same_generic_401(client: TestClient) -> None:
    expired = sign(KEY, claims(exp=1))

    response = client.get("/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_non_bearer_scheme_is_401(client: TestClient) -> None:
    response = client.get("/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})

    assert response.status_code == 401


def test_valid_token_reaches_the_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[UUID] = []

    async def get_me(user: VerifiedToken) -> MeResponse:
        seen.append(user.user_id)
        return MeResponse(user_id=user.user_id, organizations=[_org(ORG_A)])

    monkeypatch.setattr(orgs_service, "get_me", get_me)

    response = client.get("/me", headers=_auth())

    assert response.status_code == 200
    assert response.json()["user_id"] == str(USER_ID)
    assert seen == [USER_ID]


def test_auth_unconfigured_is_503_not_open(memberships: list[Membership]) -> None:
    with TestClient(create_app(Settings(_env_file=None))) as unconfigured:
        response = unconfigured.get("/me", headers=_auth())

    assert response.status_code == 503


def test_token_never_reaches_logs(client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    token = sign(KEY, claims(exp=1))

    client.get("/me", headers={"Authorization": f"Bearer {token}"})

    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert token.split(".")[2] not in captured.out + captured.err


# ----- org selection -----


def test_select_membership_rules() -> None:
    a, b = Membership(ORG_A, "owner"), Membership(ORG_B, "reviewer")

    assert select_membership([a], None) == a
    assert select_membership([a, b], ORG_B) == b
    with pytest.raises(OrgSelectionRequiredError):
        select_membership([a, b], None)
    with pytest.raises(OrgNotFoundError):
        select_membership([], None)
    with pytest.raises(OrgNotFoundError):
        select_membership([a], ORG_B)


@pytest.fixture
def stub_org_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_organization(_user: VerifiedToken, org_id: UUID) -> Organization:
        return _org(org_id)

    async def rename_organization(_user: VerifiedToken, org_id: UUID, name: str) -> Organization:
        return _org(org_id).model_copy(update={"name": name})

    monkeypatch.setattr(orgs_service, "get_organization", get_organization)
    monkeypatch.setattr(orgs_service, "rename_organization", rename_organization)


@pytest.mark.usefixtures("stub_org_reads")
def test_member_gets_their_only_org_without_header(
    client: TestClient, memberships: list[Membership]
) -> None:
    memberships.append(Membership(ORG_A, "reviewer"))

    response = client.get("/orgs/current", headers=_auth())

    assert response.status_code == 200
    assert response.json()["id"] == str(ORG_A)


@pytest.mark.usefixtures("stub_org_reads")
def test_other_orgs_id_is_404_not_403(client: TestClient, memberships: list[Membership]) -> None:
    memberships.append(Membership(ORG_A, "owner"))

    response = client.get("/orgs/current", headers={**_auth(), "X-Org-Id": str(ORG_B)})

    assert response.status_code == 404
    assert response.json() == {"detail": "Organization not found"}


@pytest.mark.usefixtures("stub_org_reads")
def test_user_without_org_gets_404(client: TestClient) -> None:
    assert client.get("/orgs/current", headers=_auth()).status_code == 404


@pytest.mark.usefixtures("stub_org_reads")
def test_multi_org_user_must_choose(client: TestClient, memberships: list[Membership]) -> None:
    memberships.extend([Membership(ORG_A, "owner"), Membership(ORG_B, "reviewer")])

    assert client.get("/orgs/current", headers=_auth()).status_code == 400
    chosen = client.get("/orgs/current", headers={**_auth(), "X-Org-Id": str(ORG_B)})
    assert chosen.json()["id"] == str(ORG_B)


@pytest.mark.usefixtures("stub_org_reads")
def test_malformed_org_header_is_422(client: TestClient, memberships: list[Membership]) -> None:
    memberships.append(Membership(ORG_A, "owner"))

    response = client.get("/orgs/current", headers={**_auth(), "X-Org-Id": "not-a-uuid"})

    assert response.status_code == 422


# ----- roles -----


@pytest.mark.usefixtures("stub_org_reads")
@pytest.mark.parametrize(("role", "expected"), [("owner", 200), ("admin", 200), ("reviewer", 403)])
def test_rename_requires_owner_or_admin(
    client: TestClient, memberships: list[Membership], role: Role, expected: int
) -> None:
    memberships.append(Membership(ORG_A, role))

    response = client.patch("/orgs/current", headers=_auth(), json={"name": "Renamed"})

    assert response.status_code == expected


# ----- create + error mapping (D4) -----


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (SlugUnavailableError, 409, "slug_unavailable"),
        (OrgLimitReachedError, 409, "organization_limit_reached"),
    ],
)
def test_create_errors_are_mapped_to_stable_codes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
    status: int,
    code: str,
) -> None:
    async def create_organization(_user: VerifiedToken, _name: str, _slug: str) -> Organization:
        raise error

    monkeypatch.setattr(orgs_service, "create_organization", create_organization)

    response = client.post("/orgs", headers=_auth(), json={"name": "Acme", "slug": "acme-supply"})

    assert response.status_code == status
    assert response.json() == {"detail": code}


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Acme", "slug": "Acme Supply"},
        {"name": "Acme", "slug": "ab"},
        {"name": "   ", "slug": "acme-supply"},
        {"name": "Acme", "slug": "acme-supply", "owner_id": str(uuid4())},
    ],
)
def test_create_validates_input_before_the_db(client: TestClient, body: dict[str, str]) -> None:
    assert client.post("/orgs", headers=_auth(), json=body).status_code == 422


class _NoKeys:
    async def get_key(self, kid: str) -> None:
        raise KeysUnavailableError


def test_jwks_unavailable_is_503_not_401(memberships: list[Membership]) -> None:
    settings = Settings(_env_file=None, supabase_url="http://supabase.test")
    with TestClient(create_app(settings, jwks=_NoKeys())) as no_keys:
        response = no_keys.get("/me", headers=_auth())

    assert response.status_code == 503
    assert "www-authenticate" not in response.headers


def test_database_busy_is_503_with_retry_after(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def create_organization(_user: VerifiedToken, _name: str, _slug: str) -> Organization:
        raise DatabaseBusyError

    monkeypatch.setattr(orgs_service, "create_organization", create_organization)

    response = client.post("/orgs", headers=_auth(), json={"name": "Acme", "slug": "acme-supply"})

    assert response.status_code == 503
    assert response.json() == {"detail": "busy"}
    assert response.headers["retry-after"] == "2"
