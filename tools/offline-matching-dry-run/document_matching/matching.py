"""Conservative, page-complete matching; no portal or permission assumptions."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import replace

from .models import (
    PAGE_MAX_PAGES,
    REPORT_MAX_ASSOCIATIONS,
    TEXT_MAX_CHARS,
    DocumentSpec,
    PageMatch,
    PreparationError,
    Roster,
    normalize_cpf,
)

CPF_RE = re.compile(r"(?<![0-9])(?:[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}|[0-9]{11})(?![0-9])")
MONTH_RE = re.compile(r"\bCOMPETENCIA\s*[:=-]?\s*(?:([0-9]{2})/([0-9]{4})|([0-9]{4})-([0-9]{2}))\b")
CNPJ_RE = re.compile(
    r"\b(?:CNPJ DO EMPREGADOR|CNPJ DA EMPRESA|EMPREGADOR CNPJ)\s*[:=-]?\s*"
    r"([0-9]{2}\.?[0-9]{3}\.?[0-9]{3}/?[0-9]{4}-?[0-9]{2})\b"
)
CPF_LABEL_RE = re.compile(r"\bCPF\b\s*[:=-]?\s*")


def _fold(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).upper()


def _name(text):
    return " ".join(re.sub(r"[^\w]+", " ", _fold(text)).split())


def _hint_states(text, document):
    folded = _fold(text)
    months = {f"{m[1]}-{m[0]}" if m[0] else f"{m[2]}-{m[3]}" for m in MONTH_RE.findall(folded)}
    month_state = "not_observed"
    if months:
        month_state = (
            "matches_declared"
            if document.monthly and months == {document.competence}
            else "conflict"
            if document.monthly
            else "observed_not_applicable"
        )
    cnpjs = {re.sub(r"[^0-9]", "", value) for value in CNPJ_RE.findall(folded)}
    employer_state = "not_observed"
    if cnpjs:
        employer_state = (
            "not_compared"
            if document.employer_cnpj is None
            else "matches_declared"
            if cnpjs == {document.employer_cnpj}
            else "conflict"
        )
    return month_state, employer_state


def _page_match(number, text, cpf_index, name_index, employees, document):
    month_hint, employer_hint = _hint_states(text, document)

    def result(ids, status, reason):
        return PageMatch(
            number,
            tuple(sorted(ids)),
            status,
            reason,
            month_hint=month_hint,
            employer_hint=employer_hint,
        )

    if "conflict" in (month_hint, employer_hint):
        return result((), "unresolved", "DOCUMENT_HINT_CONFLICT")
    if not text.strip():
        return result((), "unresolved", "UNREADABLE_PAGE")
    folded = _fold(text)
    if any(not CPF_RE.match(folded, label.end()) for label in CPF_LABEL_RE.finditer(folded)):
        return result((), "unresolved", "INVALID_CPF_IDENTITY")
    names, normalized = set(), " " + _name(text) + " "
    for name, ids in name_index.items():
        if " " + name + " " in normalized:
            names.update(ids)
    raw_cpfs = CPF_RE.findall(text)
    cpfs = set()
    for raw in raw_cpfs:
        try:
            cpfs.add(normalize_cpf(raw))
        except PreparationError:
            return result((), "unresolved", "INVALID_CPF_IDENTITY")
    if not cpfs and re.search(r"\bCPF\b", _fold(text)):
        return result((), "unresolved", "INVALID_CPF_IDENTITY")
    if cpfs:
        if any(cpf not in cpf_index for cpf in cpfs):
            return result((), "unresolved", "UNKNOWN_CPF")
        ids = set().union(*(cpf_index[cpf] for cpf in cpfs))
        if len(ids) != 1:
            return result(ids, "ambiguous", "MULTIPLE_IDENTITIES")
        if names - ids:
            return result(ids | names, "ambiguous", "IDENTITY_CONFLICT")
        employee = employees[next(iter(ids))]
        if not employee.active:
            return result(ids, "excluded", "INACTIVE_EMPLOYEE")
        return result(ids, "candidate", "EXACT_CPF")
    if len(names) > 1:
        return result(names, "ambiguous", "MULTIPLE_IDENTITIES")
    if names:
        employee = employees[next(iter(names))]
        return result(
            names,
            "suggestion" if employee.active else "excluded",
            "NAME_REQUIRES_REVIEW" if employee.active else "INACTIVE_EMPLOYEE",
        )
    return result((), "unresolved", "MISSING_IDENTITY")


def match_pages(texts: list[str], roster: Roster, document: DocumentSpec) -> list[PageMatch]:
    if (
        not isinstance(texts, list)
        or not 1 <= len(texts) <= PAGE_MAX_PAGES
        or any(not isinstance(t, str) or len(t) > TEXT_MAX_CHARS for t in texts)
    ):
        raise PreparationError("PAGE_LIMIT_EXCEEDED")
    cpf_index, name_index = defaultdict(set), defaultdict(set)
    employees = {e.employee_id: e for e in roster.employees}
    for employee in roster.employees:
        cpf_index[employee.cpf].add(employee.employee_id)
        full_name = _name(employee.name)
        if len(full_name.split()) >= 2:
            name_index[full_name].add(employee.employee_id)
    matches, associations = [], 0
    for i, text in enumerate(texts, 1):
        page = _page_match(i, text, cpf_index, name_index, employees, document)
        associations += len(page.employee_ids)
        if associations > REPORT_MAX_ASSOCIATIONS:
            raise PreparationError("REPORT_LIMIT_EXCEEDED")
        matches.append(page)
    groups, sequence, previous = defaultdict(set), 0, None
    for i, page in enumerate(matches):
        identity = (
            page.employee_ids
            if (len(page.employee_ids) == 1 and page.status in {"candidate", "suggestion"})
            else None
        )
        if identity:
            if identity != previous:
                sequence += 1
            group = f"g{sequence:04d}"
            groups[identity].add(group)
            matches[i] = replace(page, group_id=group)
        previous = identity
    for i, page in enumerate(matches):
        if page.group_id and len(groups[page.employee_ids]) > 1:
            matches[i] = replace(page, warnings=("DISJOINT_OCCURRENCE",))
    return matches
