"""Explicit local JSON inputs and a new output directory; safe fixed-code failures."""

import argparse
import csv
import hashlib
import io
import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    REPORT_MAX_BYTES,
    ROSTER_MAX_BYTES,
    SAFETY_FIELDS,
    DocumentSpec,
    PreparationError,
    Scope,
)
from .review import build_review, parse_pages


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise PreparationError("ARGUMENTS_INVALID")


@dataclass(frozen=True)
class InputSnapshot:
    path: Path
    data: bytes
    identity: tuple


def _local_path(value, error):
    value = str(value)
    if not value or value.startswith(("//", "\\\\")) or "://" in value or "\x00" in value:
        raise PreparationError(error)
    if ":" in (value[2:] if len(value) > 2 and value[1] == ":" else value):
        raise PreparationError(error)
    path = Path(value).absolute()
    if ".." in path.parts:
        raise PreparationError(error)
    try:
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise PreparationError(error)
        return path.resolve()
    except OSError:
        raise PreparationError(error) from None


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_input(value):
    path = _local_path(value, "INPUT_UNAVAILABLE")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise PreparationError("INPUT_UNAVAILABLE")
            if before.st_size > ROSTER_MAX_BYTES:
                raise PreparationError("INPUT_LIMIT_EXCEEDED")
            data = stream.read(ROSTER_MAX_BYTES + 1)
            if len(data) > ROSTER_MAX_BYTES:
                raise PreparationError("INPUT_LIMIT_EXCEEDED")
            if _identity(before) != _identity(os.fstat(stream.fileno())):
                raise PreparationError("INPUT_CHANGED")
        return InputSnapshot(path, data, _identity(before))
    except OSError:
        raise PreparationError("INPUT_UNAVAILABLE") from None


def _verify_input(snapshot):
    current = _read_input(snapshot.path)
    if current.identity != snapshot.identity or current.data != snapshot.data:
        raise PreparationError("INPUT_CHANGED")


def _csv_text(value):
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple)):
        value = "|".join(value)
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def _csv_bytes(report):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";")
    columns = (
        "page",
        "employee_ids",
        "status",
        "reason",
        "group_id",
        "warnings",
        "month_hint",
        "employer_hint",
        "review_status",
        *SAFETY_FIELDS,
    )
    writer.writerow(columns)
    for page in report["pages"]:
        row = {**page, "review_status": report["review_status"], **SAFETY_FIELDS}
        writer.writerow([_csv_text(row.get(key)) for key in columns])
    return stream.getvalue().encode("utf-8-sig")


def _json_bytes(data):
    return (json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _write_new(path, data):
    if len(data) > REPORT_MAX_BYTES:
        raise PreparationError("REPORT_LIMIT_EXCEEDED")
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _write_bundle(output, report, snapshots):
    output = _local_path(output, "OUTPUT_UNAVAILABLE")
    if output.exists():
        raise PreparationError("OUTPUT_EXISTS")
    if not output.parent.is_dir():
        raise PreparationError("OUTPUT_UNAVAILABLE")
    try:
        output.mkdir()
        marker = output / "INCOMPLETE"
        _write_new(marker, b"INCOMPLETE\n")
        data = _json_bytes(report)
        csv_data = _csv_bytes(report)
        _write_new(output / "report.json", data)
        _write_new(output / "report.csv", csv_data)
        for snapshot in snapshots:
            _verify_input(snapshot)
        complete = {
            "report_sha256": hashlib.sha256(data).hexdigest(),
            "csv_sha256": hashlib.sha256(csv_data).hexdigest(),
        }
        _write_new(output / "COMPLETE.json", _json_bytes(complete))
        marker.unlink()
        return complete["report_sha256"]
    except FileExistsError:
        raise PreparationError("OUTPUT_EXISTS") from None
    except OSError:
        raise PreparationError("OUTPUT_UNAVAILABLE") from None


def main(argv=None):
    try:
        parser = SafeParser(
            description="Local text matching and dry-run reports; uploads disabled."
        )
        for name in (
            "pages",
            "roster",
            "account",
            "company",
            "unit",
            "service",
            "document-type",
            "output",
        ):
            parser.add_argument("--" + name, required=True)
        period = parser.add_mutually_exclusive_group(required=True)
        period.add_argument("--competence")
        period.add_argument("--nonmonthly", action="store_true")
        parser.add_argument("--employer-cnpj")
        args = parser.parse_args(argv)
        scope = Scope(args.account, args.company, args.unit, args.service)
        document = DocumentSpec(
            args.document_type, args.competence, not args.nonmonthly, args.employer_cnpj
        )
        pages = _read_input(args.pages)
        roster = _read_input(args.roster)
        report = build_review(
            parse_pages(pages.data), roster.data, scope, document, datetime.now(UTC)
        )
        report["sources"] = {
            "pages_sha256": hashlib.sha256(pages.data).hexdigest(),
            "roster_sha256": hashlib.sha256(roster.data).hexdigest(),
        }
        digest = _write_bundle(args.output, report, (pages, roster))
        print(
            json.dumps(
                {
                    "counts": report["counts"],
                    "review_status": report["review_status"],
                    "report_sha256": digest,
                    **SAFETY_FIELDS,
                }
            )
        )
        return 0
    except PreparationError as error:
        print(json.dumps({"error": error.code, **SAFETY_FIELDS}))
        return 2
    except Exception:
        print(json.dumps({"error": "DRY_RUN_FAILED", **SAFETY_FIELDS}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
