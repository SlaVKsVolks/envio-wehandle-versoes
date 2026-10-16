"""Strict local contracts. Snapshot declarations are not authenticated portal evidence."""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

PAGE_MAX_PAGES = 200
ROSTER_MAX_BYTES = 10 * 1024 * 1024
ROSTER_MAX_ROWS = 20_000
TEXT_MAX_CHARS = 100_000
REPORT_MAX_ASSOCIATIONS = 20_000
REPORT_MAX_BYTES = 32 * 1024 * 1024
SAFETY_FIELDS = MappingProxyType(
    {
        "mode": "dry_run",
        "upload_enabled": False,
        "execution_status": "blocked",
        "reason_code": "UPLOADS_NOT_IMPLEMENTED",
        "network_requested": False,
        "remote_state": "not_checked",
        "authorization_status": "not_granted",
    }
)


class PreparationError(ValueError):
    """Only fixed, non-sensitive error codes cross the CLI boundary."""

    def __init__(self, code):
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", code):
            code = "PREPARATION_FAILED"
        self.code = code
        super().__init__(code)


def positive_id(value):
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,63}", value) is not None


def safe_text(value, maximum=200):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or value != value.strip():
        return False
    if any(unicodedata.category(c).startswith("C") for c in value):
        return False
    return (
        re.search(
            r"https?://|Bearer\s|access[_-]?token|refresh[_-]?token|"
            r"password\s*[:=]|senha\s*[:=]|eyJ[A-Za-z0-9_-]{10,}\.",
            value,
            re.I,
        )
        is None
    )


def normalize_cpf(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9]{11}|[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2})", value
    ):
        raise PreparationError("ROSTER_SCHEMA_INVALID")
    digits = re.sub(r"[^0-9]", "", value)
    if len(set(digits)) == 1:
        raise PreparationError("ROSTER_SCHEMA_INVALID")
    for size in (9, 10):
        digit = (sum(int(digits[i]) * (size + 1 - i) for i in range(size)) * 10) % 11
        if int(digits[size]) != (0 if digit == 10 else digit):
            raise PreparationError("ROSTER_SCHEMA_INVALID")
    return digits


def normalize_cnpj(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9]{14}|[0-9]{2}\.[0-9]{3}\.[0-9]{3}/[0-9]{4}-[0-9]{2})", value
    ):
        raise PreparationError("DOCUMENT_METADATA_INVALID")
    digits = re.sub(r"[^0-9]", "", value)
    if len(set(digits)) == 1:
        raise PreparationError("DOCUMENT_METADATA_INVALID")
    for size, weights in [
        (12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
        (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
    ]:
        remainder = sum(int(digits[i]) * weights[i] for i in range(size)) % 11
        if int(digits[size]) != (0 if remainder < 2 else 11 - remainder):
            raise PreparationError("DOCUMENT_METADATA_INVALID")
    return digits


@dataclass(frozen=True)
class Scope:
    account_id: str
    company_id: str
    unit_id: str
    service_id: str

    def __post_init__(self):
        if (
            not isinstance(self.account_id, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", self.account_id)
            or not all(positive_id(v) for v in (self.company_id, self.unit_id, self.service_id))
        ):
            raise PreparationError("SCOPE_INVALID")


@dataclass(frozen=True)
class DocumentSpec:
    document_type: str
    competence: str | None
    monthly: bool
    employer_cnpj: str | None = None

    def __post_init__(self):
        if not positive_id(self.document_type) or type(self.monthly) is not bool:
            raise PreparationError("DOCUMENT_METADATA_INVALID")
        if self.monthly:
            if (
                not isinstance(self.competence, str)
                or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", self.competence)
                or self.competence.startswith("0000")
            ):
                raise PreparationError("DOCUMENT_METADATA_INVALID")
        elif self.competence is not None:
            raise PreparationError("DOCUMENT_METADATA_INVALID")
        if self.employer_cnpj is not None:
            object.__setattr__(self, "employer_cnpj", normalize_cnpj(self.employer_cnpj))


@dataclass(frozen=True)
class Employee:
    employee_id: str
    name: str
    cpf: str = field(repr=False)
    active: bool
    scope: Scope

    def __post_init__(self):
        if (
            not positive_id(self.employee_id)
            or not safe_text(self.name)
            or not any(c.isalpha() for c in self.name)
            or type(self.active) is not bool
            or not isinstance(self.scope, Scope)
        ):
            raise PreparationError("ROSTER_SCHEMA_INVALID")
        object.__setattr__(self, "cpf", normalize_cpf(self.cpf))


@dataclass(frozen=True)
class Roster:
    scope: Scope
    observed_at: datetime
    source_reference: str
    employees: tuple[Employee, ...] = field(repr=False)
    freshness: str


@dataclass(frozen=True)
class PageMatch:
    page: int
    employee_ids: tuple[str, ...]
    status: str
    reason: str
    group_id: str | None = None
    warnings: tuple[str, ...] = ()
    month_hint: str = "not_observed"
    employer_hint: str = "not_observed"


def _object(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise PreparationError("ROSTER_SCHEMA_INVALID")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PreparationError("ROSTER_SCHEMA_INVALID")
        result[key] = value
    return result


def strict_json(raw):
    try:
        return json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PreparationError("ROSTER_SCHEMA_INVALID") from None


def _parse_scope(value):
    value = _object(value, ("account_id", "company_id", "unit_id", "service_id"))
    try:
        return Scope(**value)
    except PreparationError:
        raise PreparationError("ROSTER_SCHEMA_INVALID") from None


def parse_roster(raw: bytes, scope: Scope, now: datetime) -> Roster:
    if not isinstance(raw, bytes) or len(raw) > ROSTER_MAX_BYTES:
        raise PreparationError("ROSTER_LIMIT_EXCEEDED")
    data = _object(
        strict_json(raw),
        ("schema_version", "scope", "observed_at", "source_reference", "employees"),
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise PreparationError("ROSTER_SCHEMA_INVALID")
    declared = _parse_scope(data["scope"])
    if declared != scope:
        raise PreparationError("SCOPE_MISMATCH")
    if not safe_text(data["source_reference"], maximum=80) or not re.fullmatch(
        r"[A-Za-z0-9_.:-]{1,80}", data["source_reference"]
    ):
        raise PreparationError("ROSTER_SCHEMA_INVALID")
    stamp = data["observed_at"]
    if (
        not isinstance(stamp, str)
        or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})",
            stamp,
        )
        or not isinstance(now, datetime)
        or now.utcoffset() is None
    ):
        raise PreparationError("SNAPSHOT_TIME_INVALID")
    try:
        observed = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        raise PreparationError("SNAPSHOT_TIME_INVALID") from None
    if observed > now + timedelta(minutes=5):
        raise PreparationError("SNAPSHOT_TIME_INVALID")
    rows = data["employees"]
    if not isinstance(rows, list):
        raise PreparationError("ROSTER_SCHEMA_INVALID")
    if len(rows) > ROSTER_MAX_ROWS:
        raise PreparationError("ROSTER_LIMIT_EXCEEDED")
    employees, seen = [], set()
    for row in rows:
        row = _object(row, ("employee_id", "name", "cpf", "active", "scope"))
        employee_scope = _parse_scope(row["scope"])
        if employee_scope != scope:
            raise PreparationError("SCOPE_MISMATCH")
        worker = Employee(
            row["employee_id"], row["name"], row["cpf"], row["active"], employee_scope
        )
        if worker.employee_id in seen:
            raise PreparationError("ROSTER_SCHEMA_INVALID")
        seen.add(worker.employee_id)
        employees.append(worker)
    freshness = "stale" if now - observed > timedelta(hours=24) else "within_declared_window"
    return Roster(scope, observed, data["source_reference"], tuple(employees), freshness)
