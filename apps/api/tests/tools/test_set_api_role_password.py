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


ADMIN = (
    "postgresql://postgres.abcdefghijklmnopqrst:Adm1n-Secret@aws-0-us-east-1.pooler.supabase.com"
    ":5432/postgres"
)


def test_generated_passwords_are_40_letters_and_digits_and_valid() -> None:
    passwords = {tool.generate_password() for _ in range(50)}

    assert len(passwords) == 50
    for password in passwords:
        assert len(password) == 40
        assert password.isascii() and password.isalnum()
        tool.validate_password(password, password)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (ADMIN, f"received {len(ADMIN)} chars, starts with postgresql://: yes"),
        ("postgres://u:p@h/db", "received 19 chars, starts with postgresql://: yes"),
        ("aws-0-us-east-1.pooler.supabase.com", "received 35 chars, starts with postgresql://: no"),
        ("", "received 0 chars, starts with postgresql://: no"),
    ],
)
def test_url_input_is_described_without_content(value: str, expected: str) -> None:
    assert tool.describe_url_input(value) == expected


def test_secret_input_is_described_by_length_only() -> None:
    assert tool.describe_secret_input("Gq7#vT2!") == "received 8 chars"


def test_api_role_url_swaps_user_and_password_on_the_same_pooler() -> None:
    url = tool.api_role_url(f"{ADMIN}?sslmode=require", "Abc123")

    assert url == (
        "postgresql://po_api.abcdefghijklmnopqrst:Abc123@aws-0-us-east-1.pooler.supabase.com"
        ":5432/postgres?sslmode=require"
    )


def test_api_role_url_for_a_local_postgres_user() -> None:
    assert tool.api_role_url("postgresql://postgres:x@127.0.0.1:54322/postgres", "P1") == (
        "postgresql://po_api:P1@127.0.0.1:54322/postgres"
    )


class _Applied:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, admin_url: str, _context: object, verifier: str) -> None:
        self.calls.append((admin_url, verifier))


def test_generate_sets_a_new_password_and_prints_it_once(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    applied = _Applied()
    monkeypatch.setattr(tool, "apply_verifier", applied)
    monkeypatch.setattr(tool, "generate_password", lambda: "G" * 20 + "7" * 20)
    asked: list[str] = []

    def prompt(message: str) -> str:
        asked.append(message)
        return f"  {ADMIN}\r\n"  # stray whitespace from a paste is stripped

    code = tool.main(["--generate"], prompt=prompt)

    out = capsys.readouterr().out
    assert code == 0
    assert asked == ["postgres connection string (input hidden): "]  # no password prompts
    assert f"received {len(ADMIN)} chars, starts with postgresql://: yes" in out
    assert out.count("G" * 20 + "7" * 20) == 2  # once as the password, once inside the URL
    assert "po_api.abcdefghijklmnopqrst:" + "G" * 20 + "7" * 20 + "@" in out
    assert "Adm1n-Secret" not in out
    assert "cls" in out
    [(admin_url, verifier)] = applied.calls
    assert admin_url == ADMIN
    assert tool.VERIFIER.fullmatch(verifier)


def test_without_generate_each_hidden_input_is_confirmed(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    applied = _Applied()
    monkeypatch.setattr(tool, "apply_verifier", applied)
    answers = iter([ADMIN, f"{GOOD}\n", f" {GOOD}"])

    code = tool.main([], prompt=lambda _msg: next(answers))

    out = capsys.readouterr().out
    assert code == 0
    assert out.count(f"received {len(GOOD)} chars") == 2
    assert GOOD not in out and "Adm1n-Secret" not in out
    assert len(applied.calls) == 1


def test_a_wrong_paste_is_reported_before_anything_is_sent(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    applied = _Applied()
    monkeypatch.setattr(tool, "apply_verifier", applied)

    code = tool.main(["--generate"], prompt=lambda _msg: "aws-0-us-east-1.pooler.supabase.com")

    out = capsys.readouterr()
    assert code == 1
    assert "starts with postgresql://: no" in out.out
    assert "Expected a postgresql://" in out.err
    assert applied.calls == []


def test_cli_never_prints_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    answers = iter(["postgresql://postgres:hunter2@127.0.0.1:1/postgres", GOOD, GOOD + "x"])

    code = tool.main([], prompt=lambda _msg: next(answers))

    out = capsys.readouterr()
    assert code == 1
    assert "don't match" in out.err
    assert "hunter2" not in out.out + out.err and GOOD not in out.out + out.err
