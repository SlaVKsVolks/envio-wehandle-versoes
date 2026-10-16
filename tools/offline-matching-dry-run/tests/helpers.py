"""Synthetic-only fixtures; never import user account or personnel data."""

import copy
import importlib
import json
from datetime import UTC, datetime

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
SCOPE = {"account_id": "synthetic", "company_id": "1", "unit_id": "2", "service_id": "3"}
CPF_ALFA = "12345678909"
CPF_BETA = "98765432100"
CPF_OTHER = "11144477735"


def api(test, name):
    try:
        return importlib.import_module("document_matching." + name)
    except ModuleNotFoundError as error:
        test.fail(f"Preparation API is missing: {error.name}")


def employee(employee_id="10", name="Pessoa Sintetica Alfa", cpf=CPF_ALFA, active=True):
    return {
        "employee_id": employee_id,
        "name": name,
        "cpf": cpf,
        "active": active,
        "scope": dict(SCOPE),
    }


def roster_data():
    return {
        "schema_version": 1,
        "scope": dict(SCOPE),
        "observed_at": "2026-10-15T12:00:00Z",
        "source_reference": "synthetic-fixture",
        "employees": [employee(), employee("20", "Pessoa Sintetica Beta", CPF_BETA)],
    }


def roster_bytes(data=None):
    return json.dumps(copy.deepcopy(roster_data() if data is None else data)).encode("utf-8")
