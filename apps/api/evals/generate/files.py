"""A case's three files, and comparing them with what is committed (owner decision Q2).

Comparison per format, so a codec difference that doesn't change the content doesn't
churn the repo (and PR 9's recording hashes, which hash the source bytes, stay stable):
- PDF (text and scanned), EML, CSV, JSON: exact bytes;
- PNG / JPG: the decoded pixels (mode, size, bytes);
- XLSX: the ZIP entry names, in order, and each entry's bytes.
The generator never rewrites a file whose comparable content is unchanged.
"""

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from evals.generate import GENERATOR, GENERATOR_VERSION, expected, meta
from evals.generate.render import Rendered, render
from evals.generate.spec import Case

EXPECTED_NAME = "expected.json"
META_NAME = "meta.json"


@dataclass(frozen=True, slots=True)
class CaseFiles:
    case: Case
    rendered: Rendered
    files: dict[str, bytes]  # file name -> bytes


def case_meta(case: Case, rendered: Rendered) -> meta.CaseMeta:
    tags = sorted({case.group, case.input_type, *case.tags})
    return meta.CaseMeta(
        synthetic=True,
        schema_version=meta.META_SCHEMA_VERSION,
        generator=GENERATOR,
        generator_version=GENERATOR_VERSION,
        seed=case.seed,
        input_type=case.input_type,
        group=case.group,
        tags=tags,
        pages=rendered.pages,
        sender_email=case.sender_email,
        received_at=case.received_at,
        expected_input_warnings=list(case.expected_input_warnings),
        expected_extraction_warnings=[
            meta.WarningMeta(
                code=w.code,  # type: ignore[arg-type]  # checked by the model
                path=w.path,
                details=(
                    meta.WarningDetailsMeta(
                        names=list(w.names),
                        rows=list(w.rows),
                        texts=list(w.texts),
                        hidden_rows=w.hidden_rows,
                        hidden_columns=w.hidden_columns,
                        count=w.count,
                    )
                    if w.has_details
                    else None
                ),
            )
            for w in case.expected_extraction_warnings
        ],
        not_scored=[path for path, _ in case.not_scored],
        not_scored_reasons=dict(case.not_scored),
        known_gap=list(case.known_gap),
        known_gap_raw_texts=list(case.known_gap_raw_texts),
    )


def build_files(case: Case) -> CaseFiles:
    rendered = render(case)
    truth = expected.build(case, rendered.line_pages)
    described = case_meta(case, rendered)
    meta.check_line_paths(described, len(truth.lines))
    truths = {line.raw_text for line in truth.lines}
    if any(text in truths for text in described.known_gap_raw_texts):
        raise ValueError(f"{case.name}: a known-gap line is also a line of the truth")
    files = {
        case.source_name: rendered.source,
        EXPECTED_NAME: expected.dump(truth),
        META_NAME: meta.dump(described),
    }
    return CaseFiles(case=case, rendered=rendered, files=files)


def pixels(data: bytes) -> tuple[str, tuple[int, int], bytes]:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return image.mode, image.size, image.tobytes()


def zip_entries(data: bytes) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [(info.filename, archive.read(info)) for info in archive.infolist()]


def same_content(name: str, old: bytes, new: bytes) -> bool:
    if old == new:
        return True
    suffix = Path(name).suffix.lower()
    try:
        if suffix in (".png", ".jpg"):
            return pixels(old) == pixels(new)
        if suffix == ".xlsx":
            return zip_entries(old) == zip_entries(new)
    except (OSError, zipfile.BadZipFile, ValueError):
        return False
    return False


def problems(built: CaseFiles, root: Path) -> list[str]:
    """What differs between a fresh render and the committed case directory."""
    folder = root / built.case.name
    found: list[str] = []
    for name, data in built.files.items():
        path = folder / name
        if not path.is_file():
            found.append(f"{built.case.name}/{name}: missing")
        elif not same_content(name, path.read_bytes(), data):
            found.append(f"{built.case.name}/{name}: differs")
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.name not in built.files:
                found.append(f"{built.case.name}/{path.name}: not generated")
    return found


def write(built: CaseFiles, root: Path) -> list[str]:
    """Writes the files that changed; removes files the case no longer has."""
    folder = root / built.case.name
    folder.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, data in built.files.items():
        path = folder / name
        if path.is_file() and same_content(name, path.read_bytes(), data):
            continue
        path.write_bytes(data)
        written.append(f"{built.case.name}/{name}")
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.name not in built.files:
            path.unlink()
            written.append(f"{built.case.name}/{path.name} (removed)")
    return written
