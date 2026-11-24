"""The eval generator's core (M1 PR 6 T1): spec values, meta, expected.json, comparison, CLI."""

import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError

from evals.generate import __main__ as cli
from evals.generate import expected, files, meta
from evals.generate.spec import (
    AddressBlock,
    Case,
    Col,
    DateText,
    Header,
    Line,
    PdfDoc,
    money,
    n,
    n_as,
)

WHEN = datetime(2026, 11, 2, 14, 5, tzinfo=UTC)


# --- spec ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("printed", "value"),
    [
        ("12", "12"),
        ("$1,234.50", "1234.50"),
        ("(5.00)", "-5.00"),
        ("5.00-", "-5.00"),
        (".75", "0.75"),
    ],
)
def test_plain_numbers_are_read_from_their_printed_form(printed: str, value: str) -> None:
    assert n(printed).value == Decimal(value)
    assert n(printed).printed == printed


@pytest.mark.parametrize("printed", ["12 CS", "1.234,50", "N/C", "", "0,123"])
def test_a_number_that_needs_reading_must_be_given_by_hand(printed: str) -> None:
    with pytest.raises(ValueError, match="give its value"):
        n(printed)
    assert n_as(printed, None).value is None
    assert n_as(printed, "12").value == Decimal("12")


@pytest.mark.parametrize(
    ("value", "printed"),
    [("1234.5", "1,234.50"), ("12", "12.00"), ("999.999", "1,000.00"), ("0.8", "0.80")],
)
def test_money_always_has_two_decimals(value: str, printed: str) -> None:
    assert money(Decimal(value)) == printed


@pytest.mark.parametrize(
    ("no", "line_no"), [("1", 1), ("10", 10), ("1a", None), ("10.1", None), (None, None)]
)
def test_only_an_all_digit_printed_line_number_is_a_line_no(
    no: str | None, line_no: int | None
) -> None:
    assert Line(no=no).line_no == line_no


def test_a_printed_cell_can_differ_from_the_value() -> None:
    line = Line(qty=n_as("12 CS", "12"), uom="CS", price=n("4.50"), cells=(("uom", ""),))

    assert line.cell("qty") == "12 CS"
    assert line.cell("price") == "4.50"
    assert line.cell("uom") == ""
    assert line.cell("cpn") is None


# --- meta ----------------------------------------------------------------------------------


def _meta(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "synthetic": True,
        "schema_version": "eval_case_meta.v1",
        "generator": "evals.generate",
        "generator_version": 1,
        "seed": 7,
        "input_type": "pdf",
        "group": "clean_pdf",
        "tags": ["clean_pdf", "pdf"],
        "pages": 1,
        "sender_email": "buyer@brindlewood.example.com",
        "received_at": "2026-11-02T14:05:00Z",
        "expected_input_warnings": [],
        "expected_extraction_warnings": [],
        "not_scored": [],
        "known_gap": [],
    }
    data.update(changes)
    return data


def test_a_valid_meta_round_trips_canonically() -> None:
    loaded = meta.load(json.dumps(_meta()).encode())

    assert meta.load(meta.dump(loaded)) == loaded
    assert meta.dump(loaded).endswith(b"}\n")
    assert b"\r" not in meta.dump(loaded)
    assert json.loads(meta.dump(loaded))["received_at"] == "2026-11-02T14:05:00Z"


@pytest.mark.parametrize(
    "changes",
    [
        {"synthetic": False},
        {"synthetic": "true"},
        {"surprise": 1},
        {"schema_version": "eval_case_meta.v2"},
        {"pages": 0},
        {"group": "other"},
        {"input_type": "docx"},
        {"tags": ["Has Space"]},
        {"received_at": "2026-11-02T14:05:00+02:00"},
        {"received_at": "2026-11-02T14:05:00"},
        {"expected_input_warnings": ["made_up"]},
        {"expected_extraction_warnings": [{"code": "made_up", "path": "lines", "details": None}]},
        {"not_scored": ["header"]},
        {"not_scored": ["lines.x.qty"]},
        {"known_gap": ["sheet tail"]},
    ],
)
def test_meta_refuses_anything_unexpected(changes: dict[str, Any]) -> None:
    raw = _meta(**changes)
    with pytest.raises(ValidationError):
        meta.load(json.dumps(raw).encode())


def test_synthetic_is_required() -> None:
    raw = _meta()
    del raw["synthetic"]
    with pytest.raises(ValidationError):
        meta.load(json.dumps(raw).encode())


@pytest.mark.parametrize(
    "email",
    ["orders@example.com", "buyer@brindlewood.example.com", "a.b+c@x-y.sub.example.com"],
)
def test_sender_email_at_example_com_or_a_subdomain(email: str) -> None:
    assert meta.load(json.dumps(_meta(sender_email=email)).encode()).sender_email == email


@pytest.mark.parametrize(
    "email",
    [
        "buyer@brindlewood.example",
        "buyer@brindlewood.test",
        "buyer@notexample.com",
        "buyer@example.com.evil.invalid",
        "buyer@example.org",
        "buyer@mail.invalid",
        "no-at-sign",
        "buyer@example.com extra",
    ],
)
def test_sender_email_anywhere_else_is_refused(email: str) -> None:
    with pytest.raises(ValidationError):
        meta.load(json.dumps(_meta(sender_email=email)).encode())


@pytest.mark.parametrize(
    ("domain", "allowed"),
    [
        ("example.com", True),
        ("EXAMPLE.COM", True),
        ("a.b.example.com", True),
        ("example.net", False),
        ("myexample.com", False),
        ("example.com.invalid", False),
        ("x.example", False),
        ("x.test", False),
    ],
)
def test_allowed_domain(domain: str, allowed: bool) -> None:
    assert meta.allowed_domain(domain) is allowed


@pytest.mark.parametrize(
    "path", ["header.notes", "header.ship_to.city", "lines.0.qty", "lines.*.page"]
)
def test_not_scored_takes_field_paths(path: str) -> None:
    assert meta.load(json.dumps(_meta(not_scored=[path])).encode()).not_scored == [path]


# --- expected.json -------------------------------------------------------------------------

COLUMNS = (
    Col("no", "Line", 30),
    Col("cpn", "Item", 80),
    Col("desc", "Description", 200),
    Col("qty", "Qty", 40, "r"),
    Col("uom", "UoM", 40),
    Col("price", "Price", 60, "r"),
    Col("ext", "Amount", 70, "r"),
)


def _case(**changes: Any) -> Case:
    fields: dict[str, Any] = {
        "name": "unit_case",
        "group": "clean_pdf",
        "seed": 1,
        "header": Header(
            customer_name="Brindlewood Facilities Co.",
            po_number="4500012345",
            po_date=DateText("03/04/26", date(2026, 3, 4)),
            ship_to=AddressBlock(
                ("Brindlewood Facilities Co.", "Attn: Receiving", "4410 Larkspur Way"),
                name="Brindlewood Facilities Co.",
                attention="Receiving",
                line1="4410 Larkspur Way",
            ),
            order_total=n("$1,234.50"),
            currency="usd",
        ),
        "lines": (
            Line(
                no="1",
                cpn="BW-1",
                desc="Widget",
                qty=n("2"),
                uom="EA",
                price=n("617.25"),
                ext=n("1,234.50"),
            ),
            Line(kind="comment", desc="Call before delivery"),
            Line(kind="charge", desc="Freight", ext=n_as("N/C", None)),
        ),
        "document": PdfDoc("grid", COLUMNS),
        "sender_email": "m.ellery@brindlewood.example.com",
        "received_at": WHEN,
    }
    fields.update(changes)
    return Case(**fields)


def test_expected_is_the_spec_truth_and_valid_v1() -> None:
    truth = expected.build(_case(), [1, 1, 2])
    data = json.loads(expected.dump(truth))

    assert data["schema_version"] == "po_extraction.v1"
    assert data["header"]["order_total"] == "1234.50"
    assert data["header"]["currency"] == "USD"
    assert data["header"]["po_date"] == "2026-03-04"
    assert data["header"]["po_date_raw"] == "03/04/26"
    assert data["header"]["ship_to"]["raw_text"] == (
        "Brindlewood Facilities Co.\nAttn: Receiving\n4410 Larkspur Way"
    )
    assert data["header"]["bill_to"] is None
    first, comment, charge = data["lines"]
    assert first["raw_text"] == "1 BW-1 Widget 2 EA 617.25 1,234.50"
    assert first["line_no"] == 1
    assert first["unit_price"] == "617.25"
    assert comment == {
        "raw_text": "Call before delivery",
        "line_no": None,
        "line_type": "comment",
        "customer_part_number": None,
        "vendor_part_number": None,
        "description": "Call before delivery",
        "qty": None,
        "uom": None,
        "unit_price": None,
        "extended_price": None,
        "page": 1,
        "confidence": 1.0,
    }
    assert charge["extended_price"] is None  # "N/C": printed, not a number
    assert charge["page"] == 2
    assert data["header_confidence"] == 1.0
    assert expected.load(expected.dump(truth)) == truth


def test_expected_needs_one_page_per_line() -> None:
    with pytest.raises(ValueError, match="one page per line"):
        expected.build(_case(), [1])


def test_a_line_outside_a_pdf_table_needs_its_raw_text() -> None:
    from evals.generate.spec import EmailDoc

    case = _case(document=EmailDoc("PO", "Buyer", "orders@example.com", "hi"))
    with pytest.raises(ValueError, match="needs `raw`"):
        expected.build(case, [None, None, None])
    lines = tuple(Line(kind="comment", desc="x", raw="x") for _ in range(3))
    assert (
        expected.build(_case(document=case.document, lines=lines), [None] * 3).lines[0].raw_text
        == "x"
    )


def test_expected_refuses_what_v1_refuses() -> None:
    with pytest.raises(ValidationError):
        expected.build(_case(document_type="not_a_po"), [1, 1, 1])
    with pytest.raises(ValidationError):
        expected.build(_case(lines=(Line(kind="comment", desc="x", cells=(("desc", ""),)),)), [1])


def test_expected_json_is_canonical() -> None:
    raw = expected.dump(expected.build(_case(), [1, 1, 1]))

    assert raw.endswith(b"}\n")
    assert b"\r" not in raw
    assert list(json.loads(raw)) == [
        "schema_version",
        "document_type",
        "header",
        "header_confidence",
        "lines",
    ]


# --- comparison per format (owner decision Q2) ---------------------------------------------


def _png(level: int, color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), color).save(buffer, format="PNG", compress_level=level)
    return buffer.getvalue()


def _zip(entries: list[tuple[str, bytes]], when: tuple[int, ...]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries:
            info = zipfile.ZipInfo(name, date_time=when)  # type: ignore[arg-type]
            archive.writestr(info, payload)
    return buffer.getvalue()


def test_png_compares_pixels_not_bytes() -> None:
    a, b = _png(1), _png(9)
    assert a != b
    assert files.same_content("source.png", a, b)
    assert not files.same_content("source.png", a, _png(1, (10, 20, 31)))


def test_xlsx_compares_entries_not_zip_metadata() -> None:
    entries = [("a.xml", b"<a/>"), ("b.xml", b"<b/>")]
    a = _zip(entries, (2026, 1, 1, 0, 0, 0))
    b = _zip(entries, (2026, 2, 2, 0, 0, 0))
    assert a != b
    assert files.same_content("source.xlsx", a, b)
    assert not files.same_content("source.xlsx", a, _zip(entries[::-1], (2026, 1, 1, 0, 0, 0)))
    assert not files.same_content(
        "source.xlsx", a, _zip([("a.xml", b"<a/>")], (2026, 1, 1, 0, 0, 0))
    )


@pytest.mark.parametrize("name", ["source.pdf", "source.eml", "source.csv", "expected.json"])
def test_other_formats_compare_exact_bytes(name: str) -> None:
    assert files.same_content(name, b"abc", b"abc")
    assert not files.same_content(name, b"abc", b"abd")


def test_unreadable_files_never_compare_equal() -> None:
    assert not files.same_content("source.png", b"not a png", _png(1))
    assert not files.same_content("source.xlsx", b"not a zip", b"also not")


# --- CLI -----------------------------------------------------------------------------------


def test_cli_unknown_case_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--check", "no_such_case"], root=tmp_path) == 2
    assert "no_such_case" in capsys.readouterr().err


def test_cli_reports_a_directory_with_no_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "by_name", dict)
    (tmp_path / "stale_case").mkdir()
    (tmp_path / "private").mkdir()  # never a case
    (tmp_path / ".hidden").mkdir()

    assert cli.main(["--check"], root=tmp_path) == 1
    assert capsys.readouterr().err == "stale_case: no such case\n"
    assert cli.main([], root=tmp_path) == 1
    assert (tmp_path / "stale_case").is_dir(), "never deleted"


def test_cli_with_nothing_to_do_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "by_name", dict)
    assert cli.main(["--check"], root=tmp_path / "absent") == 0


def test_utc_offset_zero_in_another_zone_object_is_accepted() -> None:
    zero = datetime(2026, 11, 2, 14, 5, tzinfo=timezone(timedelta(0)))
    assert meta.CaseMeta.model_validate({**_meta(), "received_at": zero}).received_at == zero


def _built(source: bytes) -> files.CaseFiles:
    from evals.generate.render import Rendered

    case = _case()
    return files.CaseFiles(
        case=case,
        rendered=Rendered(source, 1, (1, 1, 1)),
        files={"source.png": source, "expected.json": b"{}\n", "meta.json": b"{}\n"},
    )


def test_write_skips_unchanged_content_and_removes_stray_files(tmp_path: Path) -> None:
    folder = tmp_path / "unit_case"
    assert files.problems(_built(_png(1)), tmp_path) == [
        "unit_case/source.png: missing",
        "unit_case/expected.json: missing",
        "unit_case/meta.json: missing",
    ]
    assert len(files.write(_built(_png(1)), tmp_path)) == 3
    (folder / "source.pdf").write_bytes(b"%PDF old source")

    assert files.problems(_built(_png(1)), tmp_path) == ["unit_case/source.pdf: not generated"]
    # Same pixels, other bytes: not rewritten (keeps recording hashes stable).
    assert files.write(_built(_png(9)), tmp_path) == ["unit_case/source.pdf (removed)"]
    assert (folder / "source.png").read_bytes() == _png(1)
    assert files.problems(_built(_png(9)), tmp_path) == []

    assert files.write(_built(_png(1, (1, 2, 3))), tmp_path) == ["unit_case/source.png"]
    assert files.problems(_built(_png(1)), tmp_path) == ["unit_case/source.png: differs"]


def test_case_meta_carries_the_case() -> None:
    from evals.generate.render import Rendered
    from evals.generate.spec import WarningSpec

    case = _case(
        tags=("letterhead",),
        not_scored=("header.ship_to.city",),
        known_gap=("M1-SHEET-TAIL",),
        expected_input_warnings=("encoding_guessed",),
        expected_extraction_warnings=(
            WarningSpec(
                "rows_skipped", "lines", rows=(3,), texts=("Total | 9.00",), has_details=True
            ),
            WarningSpec("unparsed_value", "lines.0.qty"),
        ),
    )
    data = files.case_meta(case, Rendered(b"", 2, (1, 1, 2))).model_dump(mode="json")

    assert data["tags"] == ["clean_pdf", "letterhead", "pdf"]
    assert data["pages"] == 2
    assert data["synthetic"] is True
    assert data["expected_extraction_warnings"] == [
        {
            "code": "rows_skipped",
            "path": "lines",
            "details": {
                "names": [],
                "rows": [3],
                "texts": ["Total | 9.00"],
                "hidden_rows": None,
                "hidden_columns": None,
                "count": None,
            },
        },
        {"code": "unparsed_value", "path": "lines.0.qty", "details": None},
    ]
