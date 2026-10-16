"""Text-in advisory report. No authentication, remote adapter or upload implementation."""

from dataclasses import asdict

from .matching import match_pages
from .models import (
    PAGE_MAX_PAGES,
    ROSTER_MAX_BYTES,
    SAFETY_FIELDS,
    TEXT_MAX_CHARS,
    PreparationError,
    parse_roster,
    strict_json,
)


def parse_pages(raw):
    if not isinstance(raw, bytes) or len(raw) > ROSTER_MAX_BYTES:
        raise PreparationError("PAGE_LIMIT_EXCEEDED")
    try:
        data = strict_json(raw)
    except PreparationError:
        raise PreparationError("PAGE_SCHEMA_INVALID") from None
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "pages"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or not isinstance(data["pages"], list)
        or any(not isinstance(text, str) for text in data["pages"])
    ):
        raise PreparationError("PAGE_SCHEMA_INVALID")
    if not 1 <= len(data["pages"]) <= PAGE_MAX_PAGES or any(
        len(text) > TEXT_MAX_CHARS for text in data["pages"]
    ):
        raise PreparationError("PAGE_LIMIT_EXCEEDED")
    return data["pages"]


def build_review(pages, roster_raw, scope, document, now):
    roster = parse_roster(roster_raw, scope, now)
    matches = match_pages(pages, roster, document)
    counts = dict.fromkeys(("candidate", "suggestion", "ambiguous", "excluded", "unresolved"), 0)
    for page in matches:
        counts[page.status] += 1
    blocked = roster.freshness == "stale" or any(
        page.reason == "DOCUMENT_HINT_CONFLICT" for page in matches
    )
    return {
        "schema_version": 1,
        "kind": "local_matching_review",
        "scope": asdict(scope),
        "document": {
            "document_type": document.document_type,
            "competence": document.competence,
            "monthly": document.monthly,
            "employer_check_requested": document.employer_cnpj is not None,
        },
        "snapshot": {
            "declared_observed_at": roster.observed_at.isoformat(),
            "freshness": roster.freshness,
            "origin_authenticated": False,
        },
        "review_status": "blocked" if blocked else "review_required",
        "counts": counts,
        "pages": [
            {
                **asdict(page),
                "employee_ids": list(page.employee_ids),
                "warnings": list(page.warnings),
            }
            for page in matches
        ],
        **SAFETY_FIELDS,
    }
