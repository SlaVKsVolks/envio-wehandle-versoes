"""Breaks caught: identity guessing, scope drift, unsafe fallback and silent page loss."""

import copy
import json
import unittest
from datetime import timedelta

from helpers import (
    CPF_ALFA,
    CPF_BETA,
    CPF_OTHER,
    NOW,
    SCOPE,
    api,
    roster_bytes,
    roster_data,
)


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.models = api(self, "models")
        self.matching = api(self, "matching")
        self.scope = self.models.Scope(**SCOPE)
        self.document = self.models.DocumentSpec("100", "2026-09", True)

    def roster(self, data=None):
        return self.models.parse_roster(roster_bytes(data), self.scope, NOW)

    def match(self, texts, data=None, document=None):
        return self.matching.match_pages(texts, self.roster(data), document or self.document)

    def assert_bad_roster(self, data, code):
        with self.assertRaises(self.models.PreparationError) as caught:
            self.roster(data)
        self.assertEqual(str(caught.exception), code)

    def test_exact_cpf_keeps_employee_and_page_order(self):
        pages = self.match([f"CPF: {CPF_ALFA}", f"CPF: {CPF_ALFA}", f"CPF: {CPF_BETA}"])
        self.assertEqual([p.employee_ids for p in pages], [("10",), ("10",), ("20",)])
        self.assertEqual([p.page for p in pages], [1, 2, 3])
        self.assertEqual([p.status for p in pages], ["candidate"] * 3)
        self.assertEqual([p.reason for p in pages], ["EXACT_CPF"] * 3)
        self.assertEqual([p.group_id for p in pages], ["g0001", "g0001", "g0002"])

    def test_punctuated_cpf_and_accents_are_recognized(self):
        p = self.match(["Pessoa Sintética Alfa\nCPF: 123.456.789-09"])[0]
        self.assertEqual((p.employee_ids, p.reason), (("10",), "EXACT_CPF"))

    def test_unique_full_name_is_only_a_suggestion(self):
        p = self.match(["Recibo: PESSOA SINTÉTICA ALFA"])[0]
        self.assertEqual(
            (p.employee_ids, p.status, p.reason), (("10",), "suggestion", "NAME_REQUIRES_REVIEW")
        )

    def test_substrings_and_fuzzy_names_never_select_an_employee(self):
        for text in ["Sintetica Alfa", "Pessoa Sintetica Alfaa", "Pessoa Sintetika Alfa"]:
            with self.subTest(text=text):
                self.assertEqual(self.match([text])[0].employee_ids, ())

    def test_unknown_valid_cpf_does_not_fall_back_to_matching_name(self):
        p = self.match([f"Pessoa Sintetica Alfa CPF: {CPF_OTHER}"])[0]
        self.assertEqual((p.employee_ids, p.reason), ((), "UNKNOWN_CPF"))

    def test_invalid_cpf_does_not_fall_back_to_name(self):
        for bad in ["12345678900", "11111111111", "1234", "ilegivel"]:
            with self.subTest(bad=bad):
                p = self.match(["Pessoa Sintetica Alfa CPF: " + bad])[0]
                self.assertEqual((p.employee_ids, p.reason), ((), "INVALID_CPF_IDENTITY"))

    def test_cpf_and_other_employees_name_conflict(self):
        p = self.match([f"Pessoa Sintetica Beta CPF: {CPF_ALFA}"])[0]
        self.assertEqual((p.status, p.reason), ("ambiguous", "IDENTITY_CONFLICT"))

    def test_illegible_explicit_cpf_is_not_bypassed_by_another_recognized_number(self):
        for text in [
            f"Pessoa Sintetica Alfa CPF: ilegivel; referencia: {CPF_ALFA}",
            f"Pessoa Sintetica Alfa CPF: 1234; CPF: {CPF_ALFA}",
            f"CPF: {CPF_ALFA}; CPF: ilegivel",
        ]:
            with self.subTest(text=text):
                page = self.match([text])[0]
                self.assertEqual(
                    (page.employee_ids, page.status, page.reason),
                    ((), "unresolved", "INVALID_CPF_IDENTITY"),
                )

    def test_cumulative_ambiguous_associations_fail_without_truncation(self):
        data = roster_data()
        data["employees"] = [
            {**data["employees"][0], "employee_id": str(i + 1)} for i in range(200)
        ]
        with self.assertRaises(self.models.PreparationError) as caught:
            self.match([CPF_ALFA] * 101, data)
        self.assertEqual(str(caught.exception), "REPORT_LIMIT_EXCEEDED")

    def test_two_cpfs_are_ambiguous(self):
        p = self.match([f"{CPF_ALFA} {CPF_BETA}"])[0]
        self.assertEqual((p.status, p.reason), ("ambiguous", "MULTIPLE_IDENTITIES"))

    def test_duplicate_cpfs_are_not_arbitrarily_selected(self):
        data = roster_data()
        data["employees"][1]["cpf"] = CPF_ALFA
        p = self.match([CPF_ALFA], data)[0]
        self.assertEqual((p.status, p.reason), ("ambiguous", "MULTIPLE_IDENTITIES"))

    def test_duplicate_names_are_ambiguous(self):
        data = roster_data()
        data["employees"][1]["name"] = "Pessoa Sintetica Alfa"
        p = self.match(["Pessoa Sintetica Alfa"], data)[0]
        self.assertEqual((p.status, p.reason), ("ambiguous", "MULTIPLE_IDENTITIES"))

    def test_inactive_employee_is_excluded(self):
        data = roster_data()
        data["employees"][0]["active"] = False
        p = self.match([CPF_ALFA], data)[0]
        self.assertEqual((p.status, p.reason), ("excluded", "INACTIVE_EMPLOYEE"))

    def test_unidentified_continuation_is_not_attached_to_previous_person(self):
        pages = self.match([CPF_ALFA, "Continuacao sem identidade", "", CPF_BETA])
        self.assertEqual([p.employee_ids for p in pages], [("10",), (), (), ("20",)])
        self.assertEqual(
            [p.reason for p in pages],
            ["EXACT_CPF", "MISSING_IDENTITY", "UNREADABLE_PAGE", "EXACT_CPF"],
        )

    def test_disjoint_groups_are_flagged_on_both_occurrences(self):
        pages = self.match([CPF_ALFA, CPF_BETA, CPF_ALFA])
        self.assertNotEqual(pages[0].group_id, pages[2].group_id)
        self.assertIn("DISJOINT_OCCURRENCE", pages[0].warnings)
        self.assertIn("DISJOINT_OCCURRENCE", pages[2].warnings)
        self.assertNotIn("DISJOINT_OCCURRENCE", pages[1].warnings)

    def test_reporting_month_conflict_is_not_clean_candidate(self):
        p = self.match([f"CPF: {CPF_ALFA}\nCompetencia: 08/2026"])[0]
        self.assertEqual(p.reason, "DOCUMENT_HINT_CONFLICT")
        self.assertEqual(p.status, "unresolved")

    def test_reporting_month_matching_hint_is_explicit(self):
        p = self.match([f"CPF: {CPF_ALFA}\nCompetência: 09/2026"])[0]
        self.assertEqual(p.reason, "EXACT_CPF")
        self.assertEqual(p.month_hint, "matches_declared")

    def test_missing_month_hint_is_not_claimed_verified(self):
        self.assertEqual(self.match([CPF_ALFA])[0].month_hint, "not_observed")

    def test_explicit_employer_cnpj_conflict_blocks_candidate(self):
        doc = self.models.DocumentSpec("100", "2026-09", True, "11222333000181")
        p = self.match([f"CPF: {CPF_ALFA}\nCNPJ do empregador: 04.252.011/0001-10"], document=doc)[
            0
        ]
        self.assertEqual(p.reason, "DOCUMENT_HINT_CONFLICT")

    def test_scope_mismatch_rejects_roster_and_employee(self):
        for place in ["envelope", "employee"]:
            data = roster_data()
            target = data["scope"] if place == "envelope" else data["employees"][0]["scope"]
            target["unit_id"] = "999"
            with self.subTest(place=place):
                self.assert_bad_roster(data, "SCOPE_MISMATCH")

    def test_unknown_fields_and_wrong_types_fail_closed(self):
        for change in [
            lambda d: d.update(secret="unexpected"),
            lambda d: d["employees"][0].update(active="true"),
            lambda d: d["employees"][0].update(cpf=12345678909),
            lambda d: d.update(schema_version=True),
        ]:
            data = roster_data()
            change(data)
            with self.subTest(data_type=str(change)):
                self.assert_bad_roster(data, "ROSTER_SCHEMA_INVALID")

    def test_duplicate_json_keys_are_rejected(self):
        raw = roster_bytes().replace(
            b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1'
        )
        with self.assertRaises(self.models.PreparationError) as caught:
            self.models.parse_roster(raw, self.scope, NOW)
        self.assertEqual(str(caught.exception), "ROSTER_SCHEMA_INVALID")

    def test_source_reference_cannot_smuggle_a_token(self):
        data = roster_data()
        data["source_reference"] = "Bearer SYNTHETIC_ONLY_NOT_A_CREDENTIAL"
        self.assert_bad_roster(data, "ROSTER_SCHEMA_INVALID")

    def test_duplicate_employee_ids_and_bad_cpf_are_rejected(self):
        data = roster_data()
        data["employees"].append(copy.deepcopy(data["employees"][0]))
        self.assert_bad_roster(data, "ROSTER_SCHEMA_INVALID")
        data = roster_data()
        data["employees"][0]["cpf"] = "00000000000"
        self.assert_bad_roster(data, "ROSTER_SCHEMA_INVALID")

    def test_stale_snapshot_is_explicit_without_remote_claim(self):
        data = roster_data()
        data["observed_at"] = "2026-10-13T12:00:00Z"
        self.assertEqual(self.roster(data).freshness, "stale")
        self.assertEqual(self.roster().freshness, "within_declared_window")

    def test_naive_and_excessively_future_snapshot_rejected(self):
        for value in ["2026-10-15T12:00:00", "2026-10-15T15:05:01Z"]:
            data = roster_data()
            data["observed_at"] = value
            self.assert_bad_roster(data, "SNAPSHOT_TIME_INVALID")

    def test_freshness_and_future_skew_boundaries(self):
        data = roster_data()
        data["observed_at"] = "2026-10-14T15:00:00Z"
        self.assertEqual(self.roster(data).freshness, "within_declared_window")
        self.assertEqual(
            self.models.parse_roster(
                roster_bytes(data), self.scope, NOW + timedelta(seconds=1)
            ).freshness,
            "stale",
        )
        data["observed_at"] = "2026-10-15T15:05:00Z"
        self.assertEqual(self.roster(data).freshness, "within_declared_window")

    def test_monthly_and_nonmonthly_metadata_is_strict(self):
        for args in [
            ("100", "2026-13", True),
            ("100", "2026-9", True),
            ("100", None, True),
            ("100", "2026-09", False),
            ("100", None, "false"),
            ("../100", None, False),
        ]:
            with self.subTest(args=args):
                with self.assertRaises(self.models.PreparationError):
                    self.models.DocumentSpec(*args)
        self.assertIsNone(self.models.DocumentSpec("100", None, False).competence)

    def test_limits_reject_instead_of_truncating(self):
        for raw in [
            b" " * (10 * 1024 * 1024 + 1),
            json.dumps({**roster_data(), "employees": [{}] * 20001}).encode(),
        ]:
            with self.subTest(size=len(raw)):
                with self.assertRaises(self.models.PreparationError) as caught:
                    self.models.parse_roster(raw, self.scope, NOW)
                self.assertEqual(str(caught.exception), "ROSTER_LIMIT_EXCEEDED")
        with self.assertRaises(self.models.PreparationError):
            self.match(["a"] * 201)
        with self.assertRaises(self.models.PreparationError):
            self.match(["a" * 100001])

    def test_repr_does_not_expose_unmasked_cpf(self):
        self.assertNotIn(CPF_ALFA, repr(self.roster()))
        self.assertNotIn(CPF_ALFA, repr(self.roster().employees[0]))

    def test_scope_rejects_booleans_zero_and_secret_like_selectors(self):
        for key, value in [
            ("company_id", "0"),
            ("unit_id", True),
            ("account_id", "Bearer abc"),
            ("service_id", "1/2"),
        ]:
            with self.subTest(key=key):
                bad = {**SCOPE, key: value}
                with self.assertRaises(self.models.PreparationError):
                    self.models.Scope(**bad)


if __name__ == "__main__":
    unittest.main()
