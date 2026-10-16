"""Breaks caught: lost pages, identity disclosure and accidental execution claims."""

import json
import unittest

from helpers import CPF_ALFA, CPF_BETA, NOW, SCOPE, api, roster_bytes, roster_data


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.models = api(self, "models")
        self.review = api(self, "review")
        self.scope = self.models.Scope(**SCOPE)
        self.document = self.models.DocumentSpec("100", "2026-09", True)

    def build(self, pages, data=None):
        return self.review.build_review(pages, roster_bytes(data), self.scope, self.document, NOW)

    def test_page_ledger_and_counts_preserve_every_page(self):
        report = self.build([CPF_ALFA, CPF_ALFA, CPF_BETA, "Sem identidade"])
        self.assertEqual([p["employee_ids"] for p in report["pages"]], [["10"], ["10"], ["20"], []])
        self.assertEqual([p["page"] for p in report["pages"]], [1, 2, 3, 4])
        self.assertEqual(
            report["counts"],
            {"candidate": 3, "suggestion": 0, "ambiguous": 0, "excluded": 0, "unresolved": 1},
        )
        self.assertEqual(report["pages"][0]["group_id"], report["pages"][1]["group_id"])

    def test_exact_matches_never_authorize_uploads_or_approval(self):
        report = self.build([CPF_ALFA])
        self.assertEqual(report["review_status"], "review_required")
        self.assertEqual(report["mode"], "dry_run")
        self.assertIs(report["upload_enabled"], False)
        self.assertIs(report["network_requested"], False)
        self.assertEqual(report["execution_status"], "blocked")
        self.assertEqual(report["remote_state"], "not_checked")
        self.assertEqual(report["authorization_status"], "not_granted")

    def test_reports_omit_cpf_names_and_raw_page_text(self):
        report = self.build([f"SYNTHETIC_PRIVATE_TEXT Pessoa Sintetica Alfa CPF: {CPF_ALFA}"])
        encoded = json.dumps(report)
        for forbidden in (CPF_ALFA, "Pessoa Sintetica Alfa", "SYNTHETIC_PRIVATE_TEXT"):
            self.assertNotIn(forbidden, encoded)

    def test_name_suggestion_remains_review_required(self):
        report = self.build(["Pessoa Sintetica Alfa"])
        self.assertEqual(report["pages"][0]["status"], "suggestion")
        self.assertEqual(report["review_status"], "review_required")

    def test_stale_snapshot_blocks_review_without_claiming_remote_status(self):
        data = roster_data()
        data["observed_at"] = "2026-10-01T00:00:00Z"
        report = self.build([CPF_ALFA], data)
        self.assertEqual(report["snapshot"]["freshness"], "stale")
        self.assertEqual(report["review_status"], "blocked")
        self.assertEqual(report["remote_state"], "not_checked")

    def test_context_conflict_blocks_review(self):
        report = self.build([f"CPF: {CPF_ALFA} Competencia: 08/2026"])
        self.assertEqual(report["review_status"], "blocked")
        self.assertEqual(report["pages"][0]["reason"], "DOCUMENT_HINT_CONFLICT")

    def test_pages_envelope_rejects_duplicate_keys_extra_fields_and_wrong_types(self):
        for raw in [
            b'{"schema_version":1,"schema_version":1,"pages":["x"]}',
            b'{"schema_version":true,"pages":["x"]}',
            b'{"schema_version":1,"pages":["x"],"upload_enabled":true}',
            b'{"schema_version":1,"pages":[1]}',
            b"not json",
        ]:
            with self.subTest(raw=raw), self.assertRaises(self.models.PreparationError):
                self.review.parse_pages(raw)
        self.assertEqual(
            self.review.parse_pages(b'{"schema_version":1,"pages":["x",""]}'), ["x", ""]
        )

    def test_pages_envelope_refuses_excessive_input_without_truncation(self):
        for raw in [
            b" " * (10 * 1024 * 1024 + 1),
            json.dumps({"schema_version": 1, "pages": ["x"] * 201}).encode(),
        ]:
            with self.subTest(size=len(raw)), self.assertRaises(self.models.PreparationError):
                self.review.parse_pages(raw)
