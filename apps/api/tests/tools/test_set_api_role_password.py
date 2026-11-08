import base64
import hashlib
import hmac

import pytest

from tools import set_api_role_password as tool

GOOD = "Gq7#vT2!xR9$mL4&pZ8*kW3^"  # 24 printable ASCII characters (test value)


def test_verifier_has_the_postgres_format() -> None:
    verifier = tool.scram_verifier(GOOD)

    assert tool.VERIFIER.fullmatch(verifier)
    assert verifier.startswith("SCRAM-SHA-256$4096:")
    assert GOOD not in verifier


def test_verifier_matches_rfc_5802_definitions() -> None:
    salt = bytes(range(16))
    verifier = tool.scram_verifier(GOOD, salt=salt, iterations=4096)

    salted = hashlib.pbkdf2_hmac("sha256", GOOD.encode(), salt, 4096)
    stored = hashlib.sha256(hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
    server = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda data: base64.b64encode(data).decode()  # noqa: E731
    assert verifier == f"SCRAM-SHA-256$4096:{b64(salt)}${b64(stored)}:{b64(server)}"


def test_each_verifier_uses_a_fresh_salt() -> None:
    assert tool.scram_verifier(GOOD) != tool.scram_verifier(GOOD)


@pytest.mark.parametrize(
    ("password", "confirm", "message"),
    [
        (GOOD, GOOD + "x", "don't match"),
        ("short-but-ascii!", "short-but-ascii!", "24-72"),
        ("x" * 73, "x" * 73, "24-72"),
        ("with spaces in it is not ok here", "with spaces in it is not ok here", "printable ASCII"),
        ("סיסמהבעבריתשאורכהמספיקארוך", "סיסמהבעבריתשאורכהמספיקארוך", "printable ASCII"),
    ],
)
def test_bad_passwords_are_refused(password: str, confirm: str, message: str) -> None:
    with pytest.raises(tool.ToolError, match=message):
        tool.validate_password(password, confirm)


def test_a_good_password_passes() -> None:
    tool.validate_password(GOOD, GOOD)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("postgresql://po_api.ref:pw@h:5432/postgres", "`postgres` connection string"),
        ("postgresql://postgres.ref@h:5432/postgres", "no password"),
        ("postgresql://postgres.ref:pw@h:5432/postgres?sslmode=disable", "sslmode=disable"),
        ("https://h", "postgresql://"),
    ],
)
def test_admin_url_is_checked(url: str, message: str) -> None:
    with pytest.raises(tool.ToolError, match=message):
        tool.parse_admin_url(url, None)


def test_cloud_admin_url_always_uses_tls() -> None:
    _, context = tool.parse_admin_url(
        "postgresql://postgres.abcdefghijklmnopqrst:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
        None,
    )
    assert context is not None


def test_cli_never_prints_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    answers = iter(["postgresql://postgres:hunter2@127.0.0.1:1/postgres", GOOD, GOOD + "x"])

    code = tool.main([], prompt=lambda _msg: next(answers))

    out = capsys.readouterr()
    assert code == 1
    assert "don't match" in out.err
    assert "hunter2" not in out.out + out.err and GOOD not in out.out + out.err
